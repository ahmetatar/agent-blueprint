"""Tests for abp test harness command."""

from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


class TestHarnessCli:
    def test_requires_harness_scenarios(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        blueprint = Path("agent.yml")
        blueprint.write_text(
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
""",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["test", str(blueprint)])
        assert result.exit_code == 1
        assert "no harness scenarios are defined" in result.output

    def test_filters_to_single_scenario(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  scenarios:
    - id: one
      input: {}
      expected: {}
    - id: two
      input: {}
      expected: {}
""",
        )
        seen: list[str] = []

        from agent_blueprint import harness_runner

        def fake_run_harness_scenario(ir, scenario, *, install, **kwargs):
            seen.append(scenario.id)
            return harness_runner.ScenarioResult(
                scenario_id=scenario.id,
                passed=True,
                returncode=0,
            )

        monkeypatch.setattr("agent_blueprint.cli.test_cmd.run_harness_scenario", fake_run_harness_scenario)
        result = runner.invoke(app, ["test", str(blueprint), "--scenario", "two"])
        assert result.exit_code == 0
        assert seen == ["two"]
        assert "1 passed, 0 failed" in result.output

    def test_exits_non_zero_when_scenario_fails(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  scenarios:
    - id: one
      input: {}
      expected: {}
""",
        )

        from agent_blueprint.harness_runner import ScenarioResult

        def fake_run_harness_scenario(ir, scenario, *, install, **kwargs):
            return ScenarioResult(
                scenario_id=scenario.id,
                passed=False,
                returncode=1,
                failures=["boom"],
            )

        monkeypatch.setattr("agent_blueprint.cli.test_cmd.run_harness_scenario", fake_run_harness_scenario)
        result = runner.invoke(app, ["test", str(blueprint)])
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "boom" in result.output

    def test_saves_failed_traces_by_default(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  defaults:
    llm_mode: live
    tool_mode: live
  scenarios:
    - id: case
      input:
        message: "hi"
      expected: {}
""",
        )
        seen_kwargs: list[dict] = []
        from agent_blueprint import harness_runner

        def fake_run_harness_scenario(ir, scenario, *, install, trace_store=None, save_traces="all", source_dir=None):
            seen_kwargs.append({"trace_store": trace_store, "save_traces": save_traces})
            return harness_runner.ScenarioResult(
                scenario_id=scenario.id, passed=True, returncode=0,
            )

        monkeypatch.setattr(
            "agent_blueprint.cli.test_cmd.run_harness_scenario", fake_run_harness_scenario
        )
        result = runner.invoke(app, ["test", str(blueprint)])
        assert result.exit_code == 0
        assert seen_kwargs[0]["save_traces"] == "failed"
        assert seen_kwargs[0]["trace_store"] == tmp_path / ".abp" / "traces"

    def test_save_traces_none_disables(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  defaults:
    llm_mode: live
    tool_mode: live
  scenarios:
    - id: case
      input:
        message: "hi"
      expected: {}
""",
        )
        seen_kwargs: list[dict] = []
        from agent_blueprint import harness_runner

        def fake_run_harness_scenario(ir, scenario, *, install, trace_store=None, save_traces="all", source_dir=None):
            seen_kwargs.append({"trace_store": trace_store})
            return harness_runner.ScenarioResult(
                scenario_id=scenario.id, passed=True, returncode=0,
            )

        monkeypatch.setattr(
            "agent_blueprint.cli.test_cmd.run_harness_scenario", fake_run_harness_scenario
        )
        result = runner.invoke(app, ["test", str(blueprint), "--save-traces", "none"])
        assert result.exit_code == 0
        assert seen_kwargs[0]["trace_store"] is None
