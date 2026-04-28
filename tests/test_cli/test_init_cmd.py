"""Tests for abp init templates."""

from pathlib import Path
import re

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class TestInitCommand:
    def test_spec_template_creates_markdown_by_default(self):
        with runner.isolated_filesystem():
            result = runner.invoke(app, ["init", "--template", "spec"])

            assert result.exit_code == 0
            output = Path("agent.spec.md")
            assert output.exists()
            content = output.read_text(encoding="utf-8")
            assert "# ABP Blueprint Request" in content
            assert "`blueprints/agent.yml`" in content
            assert "abp validate blueprints/agent.yml" in content
            assert "agent-blueprint YAML" in content

    def test_spec_template_output_inflects_blueprint_name(self):
        with runner.isolated_filesystem():
            result = runner.invoke(app, ["init", "--template", "spec", "--output", "support.spec.md"])

            assert result.exit_code == 0
            output = Path("support.spec.md")
            assert output.exists()
            content = output.read_text(encoding="utf-8")
            assert "`blueprints/support.yml`" in content
            assert "abp validate blueprints/support.yml" in content

    def test_spec_template_accepts_double_dash_o_alias(self):
        with runner.isolated_filesystem():
            result = runner.invoke(app, ["init", "--template", "spec", "--o", "support.spec.md"])

            assert result.exit_code == 0
            assert Path("support.spec.md").exists()

    def test_blueprint_template_creates_yaml_by_default(self):
        with runner.isolated_filesystem():
            result = runner.invoke(app, ["init"])

            assert result.exit_code == 0
            output = Path("agent.agents.yaml")
            assert output.exists()
            assert "blueprint:" in output.read_text(encoding="utf-8")

    def test_blueprint_template_output_inflects_blueprint_name(self):
        with runner.isolated_filesystem():
            result = runner.invoke(
                app,
                ["init", "--template", "blueprint", "--output", "support-agent.agents.yaml"],
            )

            assert result.exit_code == 0
            output = Path("support-agent.agents.yaml")
            assert output.exists()
            assert 'name: "support-agent"' in output.read_text(encoding="utf-8")

    def test_help_shows_template_option(self):
        result = runner.invoke(app, ["init", "--help"])
        output = strip_ansi(result.output)

        assert result.exit_code == 0
        assert "--template" in output
        assert "blueprint" in output
        assert "spec" in output
