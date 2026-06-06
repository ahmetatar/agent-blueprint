"""Tests for the abp editor CLI command."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_blueprint.cli.app import app
from agent_blueprint.editor import server

runner = CliRunner()

_BLUEPRINT = """\
blueprint:
  name: "editor-cmd-test"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

agents:
  assistant:
    model: "openai/gpt-4o"

graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
"""


def test_editor_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["editor", str(tmp_path / "nope.yml")])
    assert result.exit_code == 1
    assert "Blueprint not found" in " ".join(result.output.split())


def test_editor_launches_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blueprint = tmp_path / "bp.yml"
    blueprint.write_text(_BLUEPRINT, encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_editor(path: Path, **kwargs: object) -> None:
        captured["path"] = path
        captured.update(kwargs)
        url_callback = kwargs["url_callback"]
        assert callable(url_callback)
        url_callback("http://127.0.0.1:9999/?token=abc")

    monkeypatch.setattr(server, "run_editor", fake_run_editor)
    result = runner.invoke(
        app, ["editor", str(blueprint), "--no-open", "--port", "9999"]
    )
    assert result.exit_code == 0
    assert captured["path"] == blueprint
    assert captured["port"] == 9999
    assert captured["open_browser"] is False
    assert captured["dev"] is False
    assert "Editor running" in " ".join(result.output.split())
