"""Tests for the root ABP CLI application."""

import re

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
WS_RE = re.compile(r"\s+")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalize_ws(text: str) -> str:
    return WS_RE.sub(" ", strip_ansi(text)).strip()


def test_root_help_shows_welcome_banner():
    result = runner.invoke(app, ["--help"])
    output = normalize_ws(result.output)

    assert result.exit_code == 0
    assert "Declarative, framework-agnostic AI agent orchestration via YAML" in output
    assert "█████╗ ██████╗" in result.output


def test_no_args_shows_welcome_banner():
    result = runner.invoke(app, [])
    output = normalize_ws(result.output)

    assert result.exit_code == 0
    assert "Declarative, framework-agnostic AI agent orchestration via YAML" in output
    assert "Usage:" in result.output


def test_subcommand_help_does_not_repeat_root_banner():
    result = runner.invoke(app, ["init", "--help"])
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert "Declarative, framework-agnostic AI agent orchestration via YAML" not in output
    assert "--template" in output
