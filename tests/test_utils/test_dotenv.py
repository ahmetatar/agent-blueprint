"""Tests for .env auto-loading in load_blueprint_yaml."""

import os
from pathlib import Path

import pytest

from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent.yml"
    p.write_text(content, encoding="utf-8")
    return p


class TestDotenvAutoLoad:
    def test_loads_dotenv_from_blueprint_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        (tmp_path / ".env").write_text("MY_RG=prod-rg\n")
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "prod-rg"

    def test_existing_env_var_takes_precedence_over_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_RG", "from-shell")
        (tmp_path / ".env").write_text("MY_RG=from-dotenv\n")
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "from-shell"

    def test_falls_back_to_cwd_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_RG=cwd-rg\n")
        # Blueprint in a subdir — no .env next to it
        subdir = tmp_path / "sub"
        subdir.mkdir()
        bp = _write_blueprint(subdir, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "cwd-rg"

    def test_no_dotenv_leaves_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        # No .env, not in env → placeholder kept as-is
        assert raw["deploy"]["azure"]["resource_group"] == "${env.MY_RG}"

    def test_dotenv_ignores_comments_and_blank_lines(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        (tmp_path / ".env").write_text(
            "# this is a comment\n\nMY_RG=clean-rg\n"
        )
        bp = _write_blueprint(tmp_path, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "clean-rg"

    def test_blueprint_dir_dotenv_wins_over_cwd(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MY_RG", raising=False)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        (cwd / ".env").write_text("MY_RG=from-cwd\n")
        subdir = tmp_path / "project"
        subdir.mkdir()
        (subdir / ".env").write_text("MY_RG=from-blueprint-dir\n")
        bp = _write_blueprint(subdir, _blueprint_with("${env.MY_RG}"))
        raw = load_blueprint_yaml(bp)
        assert raw["deploy"]["azure"]["resource_group"] == "from-blueprint-dir"


def _blueprint_with(resource_group_value: str) -> str:
    return f"""\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n: {{}}
  edges: []
deploy:
  azure:
    resource_group: "{resource_group_value}"
    acr_name: "myregistry"
    container_app_env: "my-env"
"""
