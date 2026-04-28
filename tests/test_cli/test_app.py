"""Tests for the root ABP CLI application."""

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()


def test_root_help_shows_welcome_banner():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Declarative AI agent orchestration via YAML" in result.output
    assert "█████╗ ██████╗" in result.output


def test_no_args_shows_welcome_banner():
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Declarative AI agent orchestration via YAML" in result.output
    assert "Usage:" in result.output


def test_subcommand_help_does_not_repeat_root_banner():
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    assert "Declarative AI agent orchestration via YAML" not in result.output
    assert "--template" in result.output
