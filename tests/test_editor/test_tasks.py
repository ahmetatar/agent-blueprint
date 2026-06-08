"""Tests for the editor's background action tasks (phase E3a)."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agent_blueprint.editor import tasks as tasks_module
from agent_blueprint.editor.tasks import (
    ActionError,
    TaskBusyError,
    TaskManager,
    action_surface,
)
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.runners.local import LocalRunner, LocalRunResult
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

_BLUEPRINT = """\
blueprint:
  name: "tasks-test"
  version: "1.0"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

agents:
  assistant:
    model: "openai/gpt-4o"
    system_prompt: "You are a helpful assistant."

graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
"""

_HARNESS_SECTION = """\

harness:
  defaults:
    llm_mode: live
    tool_mode: live
  scenarios:
    - id: s1
      input:
        message: "hi"
    - id: s2
      input:
        message: "yo"
"""


@pytest.fixture
def blueprint_file(tmp_path: Path) -> Path:
    path = tmp_path / "bp.yml"
    path.write_text(_BLUEPRINT, encoding="utf-8")
    return path


@pytest.fixture
def harness_blueprint_file(tmp_path: Path) -> Path:
    path = tmp_path / "bp.yml"
    path.write_text(_BLUEPRINT + _HARNESS_SECTION, encoding="utf-8")
    return path


def _run_to_completion(
    path: Path, action: str, params: dict[str, Any] | None = None
) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    manager = TaskManager(path, events.append)
    record = manager.start(action, params or {})
    manager.join(timeout=30)
    assert record.status != "running"
    return record, events


def _fake_run_capture(
    returncode: int = 0,
    stdout: str = "ok",
    manifest: dict[str, Any] | None = None,
):
    def fake(self: LocalRunner, user_input: str | None = None, **kwargs: Any) -> LocalRunResult:
        return LocalRunResult(
            returncode=returncode,
            stdout=stdout,
            stderr="",
            trace_file=None,
            trace_manifest=manifest if manifest is not None else {"trace": []},
        )

    return fake


# ---------------------------------------------------------------------------
# TaskManager mechanics
# ---------------------------------------------------------------------------


def test_unknown_action_raises(blueprint_file: Path) -> None:
    manager = TaskManager(blueprint_file, lambda message: None)
    with pytest.raises(ActionError, match="unknown action"):
        manager.start("bogus", {})


def test_started_and_done_events_published(blueprint_file: Path) -> None:
    record, events = _run_to_completion(blueprint_file, "doctor")
    types = [event["type"] for event in events]
    assert types[0] == "task_started"
    assert types[-1] == "task_done"
    assert events[-1]["task"]["id"] == record.id
    assert events[-1]["task"]["status"] == record.status


def test_second_start_while_running_raises_busy(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def slow_handler(ctx: Any) -> tuple[str, dict[str, Any]]:
        release.wait(timeout=10)
        return ("passed", {})

    monkeypatch.setitem(tasks_module._HANDLERS, "doctor", slow_handler)
    manager = TaskManager(blueprint_file, lambda message: None)
    record = manager.start("doctor", {})
    try:
        with pytest.raises(TaskBusyError):
            manager.start("doctor", {})
    finally:
        release.set()
    manager.join(timeout=10)
    assert record.status == "passed"
    # The slot frees up once the first task finishes.
    second = manager.start("doctor", {})
    manager.join(timeout=30)
    assert second.status == "passed"


def test_invalid_blueprint_yields_error_status(tmp_path: Path) -> None:
    path = tmp_path / "bp.yml"
    path.write_text("blueprint:\n  version: '1.0'\n", encoding="utf-8")
    record, _ = _run_to_completion(path, "doctor")
    assert record.status == "error"
    assert "does not validate" in (record.error or "")


def test_cancel_terminates_running_subprocess(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_capture(
        self: LocalRunner, user_input: str | None = None, **kwargs: Any
    ) -> LocalRunResult:
        # Spawn a real child and hand it to the hook, like _execute does.
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if self._process_hook is not None:
            self._process_hook(process)
        process.communicate()
        return LocalRunResult(
            returncode=process.returncode, stdout="", stderr="", trace_file=None,
            trace_manifest=None,
        )

    monkeypatch.setattr(LocalRunner, "run_capture", fake_run_capture)
    manager = TaskManager(blueprint_file, lambda message: None)
    started = time.monotonic()
    record = manager.start("run", {"input": "hello", "install": False})
    for _ in range(200):  # wait for the child to register
        if manager._process is not None:
            break
        time.sleep(0.05)
    assert manager._process is not None
    assert manager.cancel() is True
    manager.join(timeout=15)
    assert record.status == "cancelled"
    assert time.monotonic() - started < 25  # nowhere near the child's sleep(30)


def test_cancel_returns_false_when_idle(blueprint_file: Path) -> None:
    manager = TaskManager(blueprint_file, lambda message: None)
    assert manager.cancel() is False


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_doctor_action_passes(blueprint_file: Path) -> None:
    record, _ = _run_to_completion(blueprint_file, "doctor")
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["errors"] == 0
    assert record.result["target"] == "langgraph"


def test_generate_action_writes_next_to_blueprint(blueprint_file: Path) -> None:
    record, _ = _run_to_completion(blueprint_file, "generate")
    assert record.status == "passed"
    assert record.result is not None
    output_dir = Path(record.result["output_dir"])
    assert output_dir == blueprint_file.parent / "tasks-test-langgraph"
    assert (output_dir / "main.py").is_file()
    assert "main.py" in record.result["files"]


def test_generate_action_unknown_target(blueprint_file: Path) -> None:
    record, _ = _run_to_completion(blueprint_file, "generate", {"target": "nope"})
    assert record.status == "error"
    assert "unknown target" in (record.error or "")


def test_test_action_without_scenarios_errors(blueprint_file: Path) -> None:
    record, _ = _run_to_completion(blueprint_file, "test")
    assert record.status == "error"
    assert "no harness scenarios" in (record.error or "")


def test_test_action_runs_all_scenarios(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, events = _run_to_completion(harness_blueprint_file, "test")
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["total"] == 2
    assert record.result["failed_count"] == 0
    kinds = [event["event"]["kind"] for event in events if event["type"] == "task_progress"]
    assert kinds == [
        "scenario_started",
        "scenario_finished",
        "scenario_started",
        "scenario_finished",
    ]


def test_test_action_scenario_filter(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, _ = _run_to_completion(harness_blueprint_file, "test", {"scenarios": ["s2"]})
    assert record.status == "passed"
    assert record.result is not None
    assert [item["scenario"] for item in record.result["scenarios"]] == ["s2"]


def test_test_action_unknown_scenario_errors(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, _ = _run_to_completion(harness_blueprint_file, "test", {"scenarios": ["nope"]})
    assert record.status == "error"
    assert "unknown scenario" in (record.error or "")


def test_test_action_failing_scenario(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture(returncode=1))
    record, _ = _run_to_completion(harness_blueprint_file, "test")
    assert record.status == "failed"
    assert record.result is not None
    assert record.result["failed_count"] == 2


def test_run_action_requires_input(blueprint_file: Path) -> None:
    record, _ = _run_to_completion(blueprint_file, "run")
    assert record.status == "error"
    assert "input" in (record.error or "")


def test_run_action_captures_output(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture(stdout="hello back"))
    record, _ = _run_to_completion(blueprint_file, "run", {"input": "hello", "install": False})
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["returncode"] == 0
    assert record.result["stdout"] == "hello back"


def test_run_action_surfaces_final_state(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "trace": [],
        "final_state": {"intent": "refund", "messages": {"__messages__": 4}},
    }
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture(manifest=manifest))
    record, _ = _run_to_completion(blueprint_file, "run", {"input": "hi", "install": False})
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["final_state"] == {
        "intent": "refund",
        "messages": {"__messages__": 4},
    }


def test_run_action_final_state_absent_is_none(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A manifest with no final_state (e.g. a run that errored before finishing)
    # must not crash and surfaces None rather than a bogus value.
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, _ = _run_to_completion(blueprint_file, "run", {"input": "hi", "install": False})
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["final_state"] is None


def test_run_action_refuses_sandboxed_blueprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bp.yml"
    path.write_text(
        _BLUEPRINT + "\nrun:\n  sandbox:\n    enabled: true\n", encoding="utf-8"
    )
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, _ = _run_to_completion(path, "run", {"input": "hello"})
    assert record.status == "error"
    assert "sandbox" in (record.error or "")


def test_gate_without_anything_to_gate_errors(blueprint_file: Path) -> None:
    record, _ = _run_to_completion(blueprint_file, "gate")
    assert record.status == "error"
    assert "nothing to gate" in (record.error or "")


def test_gate_requires_baseline(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, _ = _run_to_completion(harness_blueprint_file, "gate")
    assert record.status == "error"
    assert "no gate baseline" in (record.error or "")


def test_gate_update_baseline_then_pass(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    record, _ = _run_to_completion(
        harness_blueprint_file, "gate", {"update_baseline": True}
    )
    assert record.status == "passed"
    baseline_path = harness_blueprint_file.parent / ".abp" / "gate-baseline.json"
    assert baseline_path.is_file()
    snapshot = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert snapshot["blueprint"] == "tasks-test"

    record, _ = _run_to_completion(harness_blueprint_file, "gate")
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["passed"] is True
    assert record.result["regressions"] == []


def test_gate_refuses_red_baseline(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture(returncode=1))
    record, _ = _run_to_completion(
        harness_blueprint_file, "gate", {"update_baseline": True}
    )
    assert record.status == "failed"
    assert not (harness_blueprint_file.parent / ".abp" / "gate-baseline.json").exists()


def test_gate_detects_regression(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture())
    _run_to_completion(harness_blueprint_file, "gate", {"update_baseline": True})
    monkeypatch.setattr(LocalRunner, "run_capture", _fake_run_capture(returncode=1))
    record, events = _run_to_completion(harness_blueprint_file, "gate")
    assert record.status == "failed"
    assert record.result is not None
    assert record.result["passed"] is False
    assert any("regressed" in line for line in record.result["regressions"])


# ---------------------------------------------------------------------------
# Action surface (drives the UI's buttons)
# ---------------------------------------------------------------------------


def test_action_surface_lists_scenarios(harness_blueprint_file: Path) -> None:
    spec = BlueprintSpec.model_validate(load_blueprint_yaml(harness_blueprint_file))
    surface = action_surface(spec, harness_blueprint_file)
    assert surface["scenarios"] == ["s1", "s2"]
    assert surface["eval_suites"] == []
    assert surface["has_gate_baseline"] is False
    assert surface["sandbox"] is False


def test_action_surface_flags_baseline_and_sandbox(tmp_path: Path) -> None:
    path = tmp_path / "bp.yml"
    path.write_text(
        _BLUEPRINT + "\nrun:\n  sandbox:\n    enabled: true\n", encoding="utf-8"
    )
    baseline = tmp_path / ".abp" / "gate-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text("{}", encoding="utf-8")
    spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
    surface = action_surface(spec, path)
    assert surface["has_gate_baseline"] is True
    assert surface["sandbox"] is True


# ---------------------------------------------------------------------------
# LocalRunner process_hook (the editor's cancel handle)
# ---------------------------------------------------------------------------


def test_local_runner_process_hook_receives_child(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_blueprint.ir.compiler import compile_blueprint

    ir = compile_blueprint(BlueprintSpec.model_validate(load_blueprint_yaml(blueprint_file)))
    captured: list[subprocess.Popen[str]] = []
    runner = LocalRunner(ir, process_hook=captured.append)

    def fake_generate(self: LocalRunner) -> None:
        assert self._tempdir is not None
        (self._tempdir / "_abp_runner.py").write_text("print('hi')\n", encoding="utf-8")

    monkeypatch.setattr(LocalRunner, "_generate", fake_generate)
    result = runner.run_capture(user_input="x")
    assert result.returncode == 0
    assert result.stdout.strip() == "hi"
    assert len(captured) == 1
    assert captured[0].poll() == 0


# ---------------------------------------------------------------------------
# Trace streaming (E3b)
# ---------------------------------------------------------------------------


def test_trace_stream_tailer_handles_partial_lines(tmp_path: Path) -> None:
    path = tmp_path / "stream.jsonl"
    events: list[dict[str, Any]] = []
    tailer = tasks_module._TraceStreamTailer(path, events.append, poll_interval=0.01)
    tailer.start()
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"event": "a"}\n')
            handle.flush()
            handle.write('{"event": "b"}\n{"event"')  # torn write: incomplete tail
            handle.flush()
        time.sleep(0.1)
        assert [event["event"] for event in events] == ["a", "b"]
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(': "c"}\n')
            handle.write("not-json\n")  # garbage must be skipped, not fatal
            handle.write('{"event": "d"}\n')
    finally:
        tailer.close()
    assert [event["event"] for event in events] == ["a", "b", "c", "d"]


def _streaming_run_capture(trace_events: list[dict[str, Any]]):
    """A fake run_capture that plays the generated stream observer's part:
    appends JSON lines to the ABP_TRACE_STREAM_FILE handed in via extra_env."""

    def fake(
        self: LocalRunner,
        user_input: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> LocalRunResult:
        stream = (extra_env or {}).get("ABP_TRACE_STREAM_FILE")
        assert stream, "the editor must pass ABP_TRACE_STREAM_FILE to the runner"
        with open(stream, "a", encoding="utf-8") as handle:
            for event in trace_events:
                handle.write(json.dumps(event) + "\n")
        return LocalRunResult(
            returncode=0, stdout="ok", stderr="", trace_file=None, trace_manifest={"trace": []}
        )

    return fake


def test_test_action_streams_trace_events(
    harness_blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        LocalRunner,
        "run_capture",
        _streaming_run_capture(
            [
                {"event": "node_started", "node": "assistant"},
                {"event": "node_finished", "node": "assistant"},
            ]
        ),
    )
    record, events = _run_to_completion(harness_blueprint_file, "test", {"scenarios": ["s1"]})
    assert record.status == "passed"
    traces = [event for event in events if event["type"] == "task_trace"]
    assert [trace["event"]["event"] for trace in traces] == ["node_started", "node_finished"]
    assert all(trace["scope"] == "s1" for trace in traces)
    assert all(trace["task_id"] == record.id for trace in traces)
    # Trace events are ephemeral UI state — they must not bloat the record.
    assert all(item["kind"] != "node_started" for item in record.progress)


def test_run_action_streams_trace_events(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        LocalRunner,
        "run_capture",
        _streaming_run_capture([{"event": "node_started", "node": "assistant"}]),
    )
    record, events = _run_to_completion(blueprint_file, "run", {"input": "hi", "install": False})
    assert record.status == "passed"
    traces = [event for event in events if event["type"] == "task_trace"]
    assert len(traces) == 1
    assert traces[0]["scope"] is None


# ---------------------------------------------------------------------------
# Deploy action (E3c) — local container engines only
# ---------------------------------------------------------------------------


def _patch_deployer(monkeypatch: pytest.MonkeyPatch, *, available: bool = True) -> list[list[str]]:
    """Stub the container deployer's subprocess surface; returns the argv log."""
    from agent_blueprint.deployers.base import BaseDeployer

    calls: list[list[str]] = []

    def fake_cmd(self: Any, cmd: list[str], **kwargs: Any) -> None:
        calls.append(cmd)
        return None

    monkeypatch.setattr(BaseDeployer, "_cmd", fake_cmd)
    monkeypatch.setattr(BaseDeployer, "_probe", lambda self, cmd: available)
    return calls


def test_deploy_action_builds_and_runs_container(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_deployer(monkeypatch)
    record, _ = _run_to_completion(blueprint_file, "deploy", {"engine": "docker"})
    assert record.status == "passed"
    assert record.result is not None
    assert record.result["engine"] == "docker"
    assert record.result["url"] == "http://localhost:8080"
    assert [c[:2] for c in calls] == [
        ["docker", "build"],
        ["docker", "rm"],
        ["docker", "run"],
    ]


def test_deploy_action_podman_engine(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_deployer(monkeypatch)
    record, _ = _run_to_completion(blueprint_file, "deploy", {"engine": "podman"})
    assert record.status == "passed"
    assert calls[0][0] == "podman"


def test_deploy_action_refuses_cloud_and_missing_engine(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_deployer(monkeypatch)
    for params in ({}, {"engine": "aws"}, {"engine": "azure"}, {"engine": "gcp"}):
        record, _ = _run_to_completion(blueprint_file, "deploy", dict(params))
        assert record.status == "error"
        assert "docker, podman" in (record.error or "")


def test_deploy_action_prerequisites_not_met(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_deployer(monkeypatch, available=False)
    record, _ = _run_to_completion(blueprint_file, "deploy", {"engine": "docker"})
    assert record.status == "error"
    assert "prerequisites not met" in (record.error or "")


def test_deploy_action_reports_missing_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Secrets are collected from declared model_providers (api_key_env), the
    # same surface `abp deploy` scans.
    path = tmp_path / "bp.yml"
    path.write_text(
        _BLUEPRINT.replace(
            "agents:",
            "model_providers:\n"
            "  openai:\n"
            "    provider: openai\n"
            "    api_key_env: OPENAI_API_KEY\n"
            "\n"
            "agents:",
        ),
        encoding="utf-8",
    )
    _patch_deployer(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    record, events = _run_to_completion(path, "deploy", {"engine": "docker"})
    assert record.status == "passed"
    assert record.result is not None
    assert "OPENAI_API_KEY" in record.result["missing_secrets"]
    warn = [e for e in events if e["type"] == "task_progress"
            and e["event"]["kind"] == "secrets_missing"]
    assert warn and "OPENAI_API_KEY" in warn[0]["event"]["names"]


def test_deploy_action_failed_command_is_failed_status(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_blueprint.deployers.base import BaseDeployer

    def failing_cmd(self: Any, cmd: list[str], **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(125, cmd)

    monkeypatch.setattr(BaseDeployer, "_cmd", failing_cmd)
    monkeypatch.setattr(BaseDeployer, "_probe", lambda self, cmd: True)
    record, _ = _run_to_completion(blueprint_file, "deploy", {"engine": "docker"})
    assert record.status == "failed"
    assert record.result is not None
    assert "exit 125" in record.result["message"]


def test_action_surface_carries_deploy_platform(tmp_path: Path) -> None:
    path = tmp_path / "bp.yml"
    path.write_text(_BLUEPRINT + "\ndeploy:\n  platform: aws\n", encoding="utf-8")
    spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
    assert action_surface(spec, path)["deploy_platform"] == "aws"


def test_base_deployer_cmd_honors_process_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_blueprint.deployers.docker import DockerDeployer
    from agent_blueprint.models.deploy import DockerDeployConfig

    deployer = DockerDeployer(DockerDeployConfig(), "hook-test")
    captured: list[subprocess.Popen[str]] = []
    deployer.process_hook = captured.append

    ok = deployer._cmd([sys.executable, "-c", "print('hi')"], capture=True)
    assert ok is not None and ok.stdout.strip() == "hi"
    assert len(captured) == 1 and captured[0].poll() == 0

    with pytest.raises(subprocess.CalledProcessError):
        deployer._cmd([sys.executable, "-c", "raise SystemExit(3)"])
    assert len(captured) == 2
