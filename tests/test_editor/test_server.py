"""Tests for the abp editor server: API, token guard, UI mount, live reload."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_blueprint.editor import server

_VALID_BLUEPRINT = """\
blueprint:
  name: "editor-test"
  version: "1.0"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

agents:
  assistant:
    model: "openai/gpt-4o"
    system_prompt: "You are a helpful assistant."

graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
"""

# Missing required `blueprint.name` → pydantic ValidationError on load.
_INVALID_BLUEPRINT = """\
blueprint:
  version: "1.0"
"""


@pytest.fixture
def blueprint_file(tmp_path: Path) -> Path:
    path = tmp_path / "bp.yml"
    path.write_text(_VALID_BLUEPRINT, encoding="utf-8")
    return path


def _client(
    blueprint: Path,
    tmp_path: Path,
    token: str | None = None,
    static_dir: Path | None = None,
) -> TestClient:
    # Tests must not depend on built frontend assets (CI runs pytest before
    # `python -m build`), so the static dir is always explicit here.
    static = static_dir if static_dir is not None else tmp_path / "no-static"
    app = server.create_app(blueprint, token=token, static_dir=static)
    return TestClient(app)


def test_health(blueprint_file: Path, tmp_path: Path) -> None:
    resp = _client(blueprint_file, tmp_path).get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_blueprint_endpoint_valid(blueprint_file: Path, tmp_path: Path) -> None:
    resp = _client(blueprint_file, tmp_path).get("/api/blueprint")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "editor-test"
    assert body["valid"] is True
    assert body["error"] is None
    assert 'name: "editor-test"' in body["yaml"]


def test_blueprint_endpoint_graph_view_model(blueprint_file: Path, tmp_path: Path) -> None:
    body = _client(blueprint_file, tmp_path).get("/api/blueprint").json()
    graph = body["graph"]
    assert graph["entry_point"] == "assistant"
    node_ids = {n["id"] for n in graph["nodes"]}
    assert {"assistant", "__start__", "__end__"} <= node_ids
    kinds = {(e["source"], e["target"]): e["kind"] for e in graph["edges"]}
    assert kinds[("__start__", "assistant")] == "entry"
    assert kinds[("assistant", "__end__")] == "normal"
    assert body["lint"] == []  # this blueprint is lint-clean


def test_blueprint_endpoint_lint_findings_with_positions(tmp_path: Path) -> None:
    # `verdict` is declared but never referenced → dead-state-field warning.
    linty = _VALID_BLUEPRINT.replace(
        "      reducer: append\n",
        "      reducer: append\n    verdict:\n      type: string\n      default: null\n",
    )
    path = tmp_path / "linty.yml"
    path.write_text(linty, encoding="utf-8")
    body = _client(path, tmp_path).get("/api/blueprint").json()
    assert body["valid"] is True
    finding = next(f for f in body["lint"] if f["code"] == "dead-state-field")
    assert finding["severity"] == "warning"
    assert finding["location"] == "state.fields.verdict"
    # Mapped onto the appended line (1-based) in the raw source.
    lines = body["yaml"].splitlines()
    assert lines[finding["line"] - 1].strip() == "verdict:"


def test_blueprint_endpoint_invalid(tmp_path: Path) -> None:
    path = tmp_path / "broken.yml"
    path.write_text(_INVALID_BLUEPRINT, encoding="utf-8")
    resp = _client(path, tmp_path).get("/api/blueprint")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["name"] is None
    assert body["error"]
    assert body["yaml"]  # raw source is returned even when invalid
    assert body["graph"] is None
    assert body["lint"] == []


def test_token_guard_blocks_anonymous(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path, token="s3cret")
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health?token=wrong").status_code == 401


def test_token_guard_query_param_then_cookie(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path, token="s3cret")
    first = client.get("/api/health?token=s3cret")
    assert first.status_code == 200
    assert client.cookies.get(server.TOKEN_COOKIE) == "s3cret"
    # Subsequent in-app fetches carry no query param — the cookie passes the guard.
    assert client.get("/api/health").status_code == 200


def test_missing_ui_returns_hint(blueprint_file: Path, tmp_path: Path) -> None:
    resp = _client(blueprint_file, tmp_path).get("/")
    assert resp.status_code == 503
    assert "npm run build" in resp.json()["detail"]


def test_ui_mount_serves_index(blueprint_file: Path, tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>abp editor</html>", encoding="utf-8")
    client = _client(blueprint_file, tmp_path, static_dir=static)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "abp editor" in resp.text
    # API routes are registered before the mount and must keep precedence.
    assert client.get("/api/health").status_code == 200


def test_pick_free_port_is_bindable(blueprint_file: Path) -> None:
    port = server.pick_free_port()
    assert 0 < port < 65536


def test_run_editor_invokes_uvicorn(
    blueprint_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: object, host: str, port: int, log_level: str) -> None:
        captured.update(host=host, port=port)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    urls: list[str] = []
    server.run_editor(blueprint_file, port=4242, open_browser=False, url_callback=urls.append)
    assert captured == {"host": "127.0.0.1", "port": 4242}
    assert urls and urls[0].startswith("http://127.0.0.1:4242/?token=")


def test_ws_rejects_missing_or_bad_token(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path, token="s3cret")
    for path in ("/ws", "/ws?token=wrong"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(path):
                pass  # pragma: no cover - connection must be refused
        assert exc_info.value.code == 1008


def test_ws_pushes_file_changed_on_external_edit(blueprint_file: Path, tmp_path: Path) -> None:
    app = server.create_app(
        blueprint_file, token="s3cret", static_dir=tmp_path / "no-static", watch_debounce_ms=50
    )
    # The context manager runs the lifespan, which starts the file watcher.
    with TestClient(app) as client:
        with client.websocket_connect("/ws?token=s3cret") as ws:
            time.sleep(0.5)  # let the watcher finish initializing
            blueprint_file.write_text(
                blueprint_file.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8"
            )
            message = ws.receive_json()
    assert message == {"type": "file_changed", "path": str(blueprint_file)}


def test_run_editor_dev_mode(blueprint_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: object, host: str, port: int, log_level: str) -> None:
        captured.update(port=port)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    urls: list[str] = []
    server.run_editor(blueprint_file, dev=True, url_callback=urls.append)
    # Dev mode: fixed port for the Vite proxy, no token in the URL.
    assert captured == {"port": server.DEV_PORT}
    assert urls == [f"http://127.0.0.1:{server.DEV_PORT}/"]
