"""Tests for abp doctor command."""

from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


class TestDoctorCli:
    def test_reports_no_findings_for_clean_langgraph_blueprint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "doctor-clean"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
model_providers:
  openai_main:
    provider: openai
    api_key_env: OPENAI_API_KEY
agents:
  assistant:
    model: "gpt-4o"
    model_provider: openai_main
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
""",
        )

        result = runner.invoke(app, ["doctor", str(blueprint)])
        assert result.exit_code == 0
        assert "No doctor findings" in result.output
        assert "0 error(s), 0 warning(s)" in result.output

    def test_reports_missing_env_var_warning(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BILLING_API_KEY", raising=False)
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "doctor-env"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  assistant:
    model: "openai/gpt-4o"
tools:
  lookup_invoice:
    type: api
    method: GET
    url: "https://api.example.com/invoices/{invoice_id}"
    auth:
      type: bearer
      token_env: BILLING_API_KEY
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
""",
        )

        result = runner.invoke(app, ["doctor", str(blueprint)])
        assert result.exit_code == 0
        assert "missing-env-var" in result.output
        assert "BILLING_API_KEY" in result.output

    def test_reports_unresolved_impl_import_as_error(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "doctor-impl"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  assistant:
    model: "openai/gpt-4o"
tools:
  classify:
    type: function
    impl: "missing.module.fn"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
""",
        )

        result = runner.invoke(app, ["doctor", str(blueprint)])
        assert result.exit_code == 1
        assert "unresolved-impl-import" in result.output
        assert "tools.classify.impl" in result.output

    def test_reports_plain_target_incompatibility(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "doctor-plain"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  router:
    model: "openai/gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
    worker:
      agent: router
  edges:
    - from: router
      to: worker
    - from: worker
      to: END
""",
        )

        result = runner.invoke(app, ["doctor", str(blueprint), "--target", "plain"])
        assert result.exit_code == 1
        assert "target-incompatible-feature" in result.output
        assert "multi-node workflows" in result.output
