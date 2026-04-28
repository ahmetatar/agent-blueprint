"""Tests for the root ABP CLI application."""

import re

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


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
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert "Declarative AI agent orchestration via YAML" not in output
    assert "--template" in output
