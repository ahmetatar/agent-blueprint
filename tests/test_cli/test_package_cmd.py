"""Tests for abp package command."""

from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()

_BLUEPRINT = """\
blueprint:
  name: "pkg-test"
  description: "Packaging test agent"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
"""


def _write_blueprint(tmp_path: Path, content: str = _BLUEPRINT) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _flat(result) -> str:
    return " ".join(result.output.split())


class TestPackageCli:
    def test_packages_into_default_layout(self, tmp_path):
        path = _write_blueprint(tmp_path)
        out_dir = tmp_path / "out"
        result = runner.invoke(app, ["package", str(path), "--output-dir", str(out_dir)])

        assert result.exit_code == 0
        assert (out_dir / "pyproject.toml").is_file()
        assert (out_dir / "src" / "pkg_test" / "cli.py").is_file()
        assert (out_dir / "src" / "pkg_test" / "main.py").is_file()
        assert not (out_dir / "requirements.txt").exists()
        flat = _flat(result)
        assert "Packaged" in flat
        assert "pipx install" in flat
        assert 'pkg-test "Hello"' in flat

    def test_default_output_dir_is_blueprint_slug_cli(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["package", str(path)])
        assert result.exit_code == 0
        assert (tmp_path / "pkg-test-cli" / "pyproject.toml").is_file()

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["package", str(path), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in _flat(result)
        assert not (tmp_path / "pkg-test-cli").exists()

    def test_invalid_blueprint_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path, "blueprint:\n  name: 1\nagents: []\n")
        result = runner.invoke(app, ["package", str(path)])
        assert result.exit_code == 1
        assert "Validation error" in _flat(result)

    def test_non_langgraph_target_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["package", str(path), "--target", "plain"])
        assert result.exit_code == 1
        assert "only supports" in _flat(result)

    def test_mcp_tools_fail_with_clear_error(self, tmp_path):
        content = _BLUEPRINT.replace(
            "agents:\n  assistant:\n    model: \"gpt-4o\"\n",
            "mcp_servers:\n"
            "  fs:\n"
            "    transport: stdio\n"
            "    command: \"npx\"\n"
            "agents:\n"
            "  assistant:\n"
            "    model: \"gpt-4o\"\n"
            "    tools: [read_file]\n",
        ) + (
            "tools:\n"
            "  read_file:\n"
            "    type: mcp\n"
            "    server: fs\n"
            "    tool: read_file\n"
        )
        path = _write_blueprint(tmp_path, content)
        result = runner.invoke(app, ["package", str(path)])
        assert result.exit_code == 1
        assert "MCP tools" in _flat(result)
