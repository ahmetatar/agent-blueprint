"""Tests for abp run --sandbox / --engine CLI plumbing."""

from pathlib import Path

from typer.testing import CliRunner

import agent_blueprint.runners.sandbox as sandbox_mod
from agent_blueprint.cli.app import app
from agent_blueprint.runners.local import LocalRunner
from agent_blueprint.runners.sandbox import SandboxRunner

runner = CliRunner()

_BLUEPRINT = """\
blueprint:
  name: "run-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
"""

_BLUEPRINT_SANDBOX_ENABLED = _BLUEPRINT + """\
run:
  sandbox:
    enabled: true
    engine: podman
"""


def _write_blueprint(tmp_path: Path, content: str = _BLUEPRINT) -> Path:
    bp = tmp_path / "agent.yml"
    bp.write_text(content, encoding="utf-8")
    return bp


def _stub_runners(monkeypatch, record: dict) -> None:
    """Stub both runner classes so no real generation/containers happen."""
    monkeypatch.setattr(sandbox_mod, "resolve_engine", lambda req: "podman")

    def fake_sandbox_run(self, *args, **kwargs):
        record["runner"] = "sandbox"
        record["engine"] = self.engine
        return 0

    def fake_local_run(self, *args, **kwargs):
        record["runner"] = "local"
        return 0

    monkeypatch.setattr(SandboxRunner, "run", fake_sandbox_run)
    monkeypatch.setattr(LocalRunner, "run", fake_local_run)


class TestRunSandboxFlag:
    def test_sandbox_flag_uses_sandbox_runner(self, tmp_path, monkeypatch):
        record: dict = {}
        _stub_runners(monkeypatch, record)
        bp = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["run", str(bp), "hi", "--sandbox"])
        assert result.exit_code == 0
        assert record["runner"] == "sandbox"
        assert "Sandbox engine" in " ".join(result.output.split())

    def test_default_is_local_runner(self, tmp_path, monkeypatch):
        record: dict = {}
        _stub_runners(monkeypatch, record)
        bp = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["run", str(bp), "hi"])
        assert result.exit_code == 0
        assert record["runner"] == "local"

    def test_blueprint_enabled_sandbox_without_flag(self, tmp_path, monkeypatch):
        record: dict = {}
        _stub_runners(monkeypatch, record)
        bp = _write_blueprint(tmp_path, _BLUEPRINT_SANDBOX_ENABLED)
        result = runner.invoke(app, ["run", str(bp), "hi"])
        assert result.exit_code == 0
        assert record["runner"] == "sandbox"

    def test_no_sandbox_overrides_blueprint(self, tmp_path, monkeypatch):
        record: dict = {}
        _stub_runners(monkeypatch, record)
        bp = _write_blueprint(tmp_path, _BLUEPRINT_SANDBOX_ENABLED)
        result = runner.invoke(app, ["run", str(bp), "hi", "--no-sandbox"])
        assert result.exit_code == 0
        assert record["runner"] == "local"

    def test_invalid_engine_rejected(self, tmp_path, monkeypatch):
        record: dict = {}
        _stub_runners(monkeypatch, record)
        bp = _write_blueprint(tmp_path)
        result = runner.invoke(
            app, ["run", str(bp), "hi", "--sandbox", "--engine", "containerd"]
        )
        assert result.exit_code == 1
        assert "Invalid engine" in " ".join(result.output.split())

    def test_engine_flag_overrides_blueprint(self, tmp_path, monkeypatch):
        record: dict = {}
        seen: dict = {}
        _stub_runners(monkeypatch, record)

        def fake_resolve(requested):
            seen["requested"] = requested
            return "docker"

        monkeypatch.setattr(sandbox_mod, "resolve_engine", fake_resolve)
        bp = _write_blueprint(tmp_path, _BLUEPRINT_SANDBOX_ENABLED)
        result = runner.invoke(app, ["run", str(bp), "hi", "--engine", "docker"])
        assert result.exit_code == 0
        assert str(seen["requested"].value) == "docker"
        assert record["engine"] == "docker"
