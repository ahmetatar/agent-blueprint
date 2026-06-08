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
    assert graph["state_fields"] == ["messages"]  # drives edge-condition chips
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
    assert message == {"type": "file_changed", "path": str(blueprint_file), "origin": "disk"}


def test_save_yaml_valid_writes_file(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    updated = _VALID_BLUEPRINT.replace("editor-test", "renamed")
    resp = client.put("/api/blueprint/yaml", json={"yaml": updated})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["name"] == "renamed"
    assert blueprint_file.read_text(encoding="utf-8") == updated


def test_save_yaml_spec_invalid_still_writes(blueprint_file: Path, tmp_path: Path) -> None:
    # The file is the source of truth: spec-invalid (but parseable) content is
    # written, exactly as an external editor could; the error rides back in
    # the response instead of blocking the save.
    client = _client(blueprint_file, tmp_path)
    resp = client.put("/api/blueprint/yaml", json={"yaml": _INVALID_BLUEPRINT})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["error"]
    assert blueprint_file.read_text(encoding="utf-8") == _INVALID_BLUEPRINT


def test_save_yaml_syntax_error_rejected_file_untouched(
    blueprint_file: Path, tmp_path: Path
) -> None:
    client = _client(blueprint_file, tmp_path)
    for bad in ("foo: [unclosed", "", "- just\n- a list\n"):
        resp = client.put("/api/blueprint/yaml", json={"yaml": bad})
        assert resp.status_code == 422
        assert resp.json()["detail"]
    assert blueprint_file.read_text(encoding="utf-8") == _VALID_BLUEPRINT


def test_save_yaml_pushes_file_changed_to_other_tabs(
    blueprint_file: Path, tmp_path: Path
) -> None:
    # No `with` block → no lifespan → no file watcher: the only possible WS
    # event is the save handler's own push, making this deterministic.
    client = _client(blueprint_file, tmp_path)
    with client.websocket_connect("/ws") as ws:
        updated = _VALID_BLUEPRINT + "# saved\n"
        assert client.put("/api/blueprint/yaml", json={"yaml": updated}).status_code == 200
        message = ws.receive_json()
    assert message == {"type": "file_changed", "path": str(blueprint_file), "origin": "save"}


def test_watcher_suppresses_own_write_echo(blueprint_file: Path) -> None:
    # The watcher drops change events whose content hash matches the last API
    # write (that save already pushed its own event); anything else — other
    # content, no recorded write, unreadable file — must broadcast.
    own = blueprint_file.read_text(encoding="utf-8")
    own_hash = server._content_hash(own)
    assert server._is_own_write_echo(blueprint_file, lambda: own_hash) is True
    assert server._is_own_write_echo(blueprint_file, lambda: "different") is False
    assert server._is_own_write_echo(blueprint_file, lambda: None) is False
    assert server._is_own_write_echo(blueprint_file.parent / "gone.yml", lambda: own_hash) is False


def test_blueprint_endpoint_includes_content_hash(blueprint_file: Path, tmp_path: Path) -> None:
    body = _client(blueprint_file, tmp_path).get("/api/blueprint").json()
    assert body["hash"] == server._content_hash(_VALID_BLUEPRINT)


def test_ops_endpoint_applies_validates_and_writes(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    base = client.get("/api/blueprint").json()["hash"]
    resp = client.post(
        "/api/blueprint/ops",
        json={
            "base_hash": base,
            "ops": [
                {
                    "op": "add_node",
                    "node_id": "helper",
                    "node": {"agent": "assistant", "description": "Helps out"},
                },
                {
                    "op": "add_edge",
                    "from_node": "assistant",
                    "target": "helper",
                    "condition": "state.needs_help",
                },
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["hash"] != base
    content = blueprint_file.read_text(encoding="utf-8")
    assert body["yaml"] == content
    assert "helper:" in content
    assert "condition: state.needs_help" in content
    # The scalar `to: END` was normalized, keeping END as the default route.
    assert "- default: END" in content
    # Untouched regions keep their authored style.
    assert 'name: "editor-test"' in content


def test_ops_endpoint_stale_hash_conflict(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    resp = client.post(
        "/api/blueprint/ops",
        json={"base_hash": "deadbeef", "ops": [{"op": "remove_node", "node_id": "assistant"}]},
    )
    assert resp.status_code == 409
    assert "changed underneath" in resp.json()["detail"]
    assert blueprint_file.read_text(encoding="utf-8") == _VALID_BLUEPRINT


def test_ops_endpoint_op_error_rejected(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    base = client.get("/api/blueprint").json()["hash"]
    resp = client.post(
        "/api/blueprint/ops",
        json={"base_hash": base, "ops": [{"op": "remove_node", "node_id": "ghost"}]},
    )
    assert resp.status_code == 422
    assert "does not exist" in resp.json()["detail"]
    assert blueprint_file.read_text(encoding="utf-8") == _VALID_BLUEPRINT


def test_ops_endpoint_validation_failure_writes_nothing(
    blueprint_file: Path, tmp_path: Path
) -> None:
    # Removing the entry-point node leaves a spec-invalid blueprint: canvas
    # ops are strict (unlike whole-file saves), so nothing is written.
    client = _client(blueprint_file, tmp_path)
    base = client.get("/api/blueprint").json()["hash"]
    resp = client.post(
        "/api/blueprint/ops",
        json={"base_hash": base, "ops": [{"op": "remove_node", "node_id": "assistant"}]},
    )
    assert resp.status_code == 422
    assert "entry_point" in resp.json()["detail"]
    assert blueprint_file.read_text(encoding="utf-8") == _VALID_BLUEPRINT


def test_ops_endpoint_pushes_file_changed(blueprint_file: Path, tmp_path: Path) -> None:
    # No lifespan → no watcher: the only WS event is the ops handler's push.
    client = _client(blueprint_file, tmp_path)
    base = client.get("/api/blueprint").json()["hash"]
    with client.websocket_connect("/ws") as ws:
        resp = client.post(
            "/api/blueprint/ops",
            json={
                "base_hash": base,
                "ops": [
                    {"op": "set_field", "path": "graph.nodes.assistant.description", "value": "x"}
                ],
            },
        )
        assert resp.status_code == 200
        message = ws.receive_json()
    assert message["origin"] == "save"


def test_set_edge_condition_op_via_api(blueprint_file: Path, tmp_path: Path) -> None:
    # Adding a condition to the assistant→END default route, end to end.
    client = _client(blueprint_file, tmp_path)
    base = client.get("/api/blueprint").json()["hash"]
    resp = client.post(
        "/api/blueprint/ops",
        json={
            "base_hash": base,
            "ops": [
                {
                    "op": "set_edge_condition",
                    "from_node": "assistant",
                    "target": "END",
                    "condition": None,
                    "new_condition": "state.messages",
                }
            ],
        },
    )
    assert resp.status_code == 200
    content = blueprint_file.read_text(encoding="utf-8")
    assert "condition: state.messages" in content
    assert "      to: END\n" not in content  # scalar normalized to the list form


def test_add_handoff_node_via_api_validates_and_writes(
    blueprint_file: Path, tmp_path: Path
) -> None:
    # The add-node dialog can create non-agent node types; a handoff node is
    # valid standalone (channel defaults), so it round-trips through the
    # strict validate-before-write path.
    client = _client(blueprint_file, tmp_path)
    base = client.get("/api/blueprint").json()["hash"]
    resp = client.post(
        "/api/blueprint/ops",
        json={
            "base_hash": base,
            "ops": [
                {
                    "op": "add_node",
                    "node_id": "escalate",
                    "node": {"type": "handoff", "channel": "console"},
                }
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert any(n["id"] == "escalate" and n["type"] == "handoff" for n in body["graph"]["nodes"])


def test_validate_expression_accepts_valid_condition(
    blueprint_file: Path, tmp_path: Path
) -> None:
    resp = _client(blueprint_file, tmp_path).post(
        "/api/expression/validate",
        json={"expression": "state.priority == 'high' and state.count > 2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["error"] is None
    assert set(body["referenced_fields"]) == {"priority", "count"}


def test_validate_expression_reports_invalid_condition(
    blueprint_file: Path, tmp_path: Path
) -> None:
    resp = _client(blueprint_file, tmp_path).post(
        "/api/expression/validate",
        json={"expression": "state.priority =="},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["error"]  # a non-empty message to show inline
    assert body["referenced_fields"] == []


def test_schema_endpoint_serves_blueprint_json_schema(
    blueprint_file: Path, tmp_path: Path
) -> None:
    resp = _client(blueprint_file, tmp_path).get("/api/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert "NodeDef" in body["$defs"]
    assert "AgentDef" in body["$defs"]
    assert "graph" in body["properties"]


def test_layout_roundtrip_via_api(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    assert client.get("/api/blueprint").json()["layout"] == {}
    resp = client.put("/api/layout", json={"positions": {"assistant": {"x": 10.5, "y": -3}}})
    assert resp.status_code == 200
    assert client.get("/api/blueprint").json()["layout"] == {"assistant": {"x": 10.5, "y": -3.0}}
    assert (blueprint_file.parent / ".abp" / "editor-layout.json").is_file()


def test_layout_rejects_malformed_positions(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    resp = client.put("/api/layout", json={"positions": {"assistant": {"x": "left"}}})
    assert resp.status_code == 422


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


# ---------------------------------------------------------------------------
# Background action tasks (phase E3a)
# ---------------------------------------------------------------------------


def _poll_current_task(client: TestClient, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get("/api/tasks/current").json()["task"]
        if task is not None and task["status"] != "running":
            return task
        time.sleep(0.05)
    raise AssertionError("task did not finish in time")


def test_blueprint_endpoint_actions_surface(blueprint_file: Path, tmp_path: Path) -> None:
    body = _client(blueprint_file, tmp_path).get("/api/blueprint").json()
    assert body["actions"] == {
        "scenarios": [],
        "eval_suites": [],
        "has_gate_baseline": False,
        "sandbox": False,
        "deploy_platform": None,
    }


def test_blueprint_endpoint_actions_none_when_invalid(tmp_path: Path) -> None:
    path = tmp_path / "bp.yml"
    path.write_text(_INVALID_BLUEPRINT, encoding="utf-8")
    body = _client(path, tmp_path).get("/api/blueprint").json()
    assert body["actions"] is None


def test_action_task_lifecycle(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    assert client.get("/api/tasks/current").json()["task"] is None
    resp = client.post("/api/actions/doctor", json={})  # params defaults to {}
    assert resp.status_code == 200
    started = resp.json()["task"]
    assert started["action"] == "doctor"
    task = _poll_current_task(client)
    assert task["id"] == started["id"]
    assert task["status"] == "passed"
    assert task["result"]["errors"] == 0


def test_action_unknown_name_404(blueprint_file: Path, tmp_path: Path) -> None:
    resp = _client(blueprint_file, tmp_path).post("/api/actions/bogus", json={"params": {}})
    assert resp.status_code == 404


def test_action_busy_409(
    blueprint_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from agent_blueprint.editor import tasks as tasks_module

    release = threading.Event()

    def slow_handler(ctx: object) -> tuple[str, dict]:
        release.wait(timeout=10)
        return ("passed", {})

    monkeypatch.setitem(tasks_module._HANDLERS, "doctor", slow_handler)
    client = _client(blueprint_file, tmp_path)
    assert client.post("/api/actions/doctor", json={"params": {}}).status_code == 200
    try:
        resp = client.post("/api/actions/doctor", json={"params": {}})
        assert resp.status_code == 409
        assert "running" in resp.json()["detail"]
    finally:
        release.set()
    _poll_current_task(client)


def test_cancel_endpoint_idle_returns_false(blueprint_file: Path, tmp_path: Path) -> None:
    resp = _client(blueprint_file, tmp_path).post("/api/tasks/current/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": False}


def test_ws_pushes_task_events(blueprint_file: Path, tmp_path: Path) -> None:
    # Lifespan client: the loop is captured, so worker-thread events reach /ws.
    app = server.create_app(blueprint_file, static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert client.post("/api/actions/doctor", json={"params": {}}).status_code == 200
            first = ws.receive_json()
            assert first["type"] == "task_started"
            assert first["task"]["action"] == "doctor"
            message = ws.receive_json()
            while message["type"] == "task_progress":
                message = ws.receive_json()
            assert message["type"] == "task_done"
            assert message["task"]["status"] == "passed"


# ---------------------------------------------------------------------------
# Chat session endpoints (phase E5.1)
# ---------------------------------------------------------------------------

_FAKE_CHAT_MAIN = """\
def run(user_input, thread_id="default"):
    return "echo:%s" % user_input
"""


def _fake_materialize(main_source: str):
    import tempfile

    def fake(self: object, *, install: bool = True) -> Path:
        tempdir = Path(tempfile.mkdtemp(prefix="abp_chat_srv_"))
        (tempdir / "main.py").write_text(main_source, encoding="utf-8")
        return tempdir

    return fake


def test_chat_state_starts_idle(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    body = client.get("/api/chat").json()["chat"]
    assert body["status"] == "idle"
    assert body["thread_id"] is None
    assert body["history"] == []


def test_chat_send_empty_message_rejected(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    assert client.post("/api/chat/send", json={"message": "   "}).status_code == 422


def test_chat_send_without_session_is_conflict(blueprint_file: Path, tmp_path: Path) -> None:
    client = _client(blueprint_file, tmp_path)
    resp = client.post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 409
    assert "start one first" in resp.json()["detail"]


def test_chat_start_then_send_roundtrip(
    blueprint_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_blueprint.editor.session import ChatSession

    monkeypatch.setattr(
        ChatSession, "_materialize_project", _fake_materialize(_FAKE_CHAT_MAIN)
    )
    app = server.create_app(blueprint_file, token=None, static_dir=tmp_path / "no-static")
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            started = client.post("/api/chat/start", json={"install": False}).json()["chat"]
            assert started["status"] in ("starting", "ready")
            # Wait for the worker thread to report ready over the WS.
            message = ws.receive_json()
            while not (message["type"] == "chat_status" and message["status"] == "ready"):
                message = ws.receive_json()
            thread_id = message["thread_id"]
            assert thread_id.startswith("editor-")

            client.post("/api/chat/send", json={"message": "hi"})
            # First the echoed user turn, then the agent reply.
            roles = []
            while len(roles) < 2:
                event = ws.receive_json()
                if event["type"] == "chat_message":
                    roles.append((event["message"]["role"], event["message"]["content"]))
            assert roles == [("user", "hi"), ("agent", "echo:hi")]

        state = client.get("/api/chat").json()["chat"]
        assert [m["role"] for m in state["history"]] == ["user", "agent"]
