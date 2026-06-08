"""Local FastAPI server behind `abp editor`.

Serves the embedded UI (built frontend assets shipped inside the wheel) and a
thin HTTP API over the existing logic modules. The blueprint YAML file stays
the single source of truth; this server is a view over it.

This module imports FastAPI at the top level on purpose: it must only be
imported when the `editor` extra is installed. `cli/editor_cmd.py` does the
lazy import and prints the install hint.
"""

import asyncio
import contextlib
import hashlib
import io
import secrets
import socket
import threading
import webbrowser
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from watchfiles import awatch

from agent_blueprint.editor import layout_store
from agent_blueprint.editor.diagnostics import lint_with_positions
from agent_blueprint.editor.ops import EditOp, OpError, apply_ops
from agent_blueprint.editor.session import ChatError, ChatSession
from agent_blueprint.editor.tasks import ActionError, TaskBusyError, TaskManager, action_surface
from agent_blueprint.editor.viewmodel import build_view_model
from agent_blueprint.exceptions import BlueprintValidationError, ExpressionError
from agent_blueprint.ir.expression import analyze_expression, parse_expression
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import (
    load_blueprint_document,
    load_blueprint_yaml,
    resolve_blueprint_data,
)
from agent_blueprint.utils.yaml_loader import yaml as _ruamel_yaml

STATIC_DIR = Path(__file__).parent / "static"
TOKEN_COOKIE = "abp_editor_token"
DEV_PORT = 8321  # fixed so the Vite dev-server proxy has a stable target

_MISSING_UI_HINT = (
    "Editor UI assets are not built. From PyPI: pip install 'agent-blueprint[editor]'. "
    "From a source checkout: cd frontend && npm install && npm run build"
)


def _abp_version() -> str:
    try:
        return version("agent-blueprint")
    except PackageNotFoundError:  # pragma: no cover - only outside an install
        return "unknown"


def blueprint_info(path: Path) -> dict[str, Any]:
    """Raw YAML, validation status, graph view-model, lint findings, layout."""
    raw_text = path.read_text(encoding="utf-8")
    name: str | None = None
    error: str | None = None
    spec: BlueprintSpec | None = None
    try:
        spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
        name = spec.blueprint.name
    except (BlueprintValidationError, ValidationError) as e:
        error = str(e)
    return {
        "path": str(path),
        "name": name,
        "valid": error is None,
        "error": error,
        "yaml": raw_text,
        "graph": build_view_model(spec) if spec is not None else None,
        "lint": lint_with_positions(spec, raw_text) if spec is not None else [],
        # What the Actions pane can offer (scenario ids, eval suites, …).
        "actions": action_surface(spec, path) if spec is not None else None,
        "layout": layout_store.load_layout(path),
        # Canvas ops send this back as base_hash so the server can detect
        # "file changed underneath" instead of mutating a stale document.
        "hash": _content_hash(raw_text),
    }


def yaml_syntax_error(text: str) -> str | None:
    """None if `text` parses as a top-level YAML mapping, else the error to show.

    This is the only gate on whole-file saves: spec-invalid content is still
    written (the file is the source of truth, and external editors can save
    invalid specs too) — the validation error just comes back in the response.
    """
    try:
        raw = _ruamel_yaml.load(text)
    except YAMLError as e:
        return str(e)
    if raw is None:
        return "Blueprint must not be empty"
    if not isinstance(raw, CommentedMap):
        return "Expected a YAML mapping at the top level"
    return None


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class YamlSaveRequest(BaseModel):
    yaml: str


class OpsRequest(BaseModel):
    base_hash: str
    ops: list[EditOp]


class NodePosition(BaseModel):
    x: float
    y: float


class LayoutSaveRequest(BaseModel):
    positions: dict[str, NodePosition]


class ActionRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ExpressionRequest(BaseModel):
    expression: str


class ChatStartRequest(BaseModel):
    install: bool = True


class ChatSendRequest(BaseModel):
    message: str


def check_expression(expression: str) -> dict[str, Any]:
    """Validate an edge condition with the same parser the generator uses.

    Returns ``{valid, error, referenced_fields}`` — the canvas edge-condition
    editor calls this live so an invalid condition never reaches a write.
    """
    try:
        compiled = parse_expression(expression)
    except ExpressionError as e:
        return {"valid": False, "error": str(e), "referenced_fields": []}
    analysis = analyze_expression(compiled)
    return {"valid": True, "error": None, "referenced_fields": sorted(analysis.referenced_fields)}


class _WsBroadcaster:
    """Tracks connected editor tabs and fans events out to all of them."""

    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def broadcast(self, message: dict[str, Any]) -> None:
        for ws in list(self.connections):
            try:
                await ws.send_json(message)
            except Exception:
                self.connections.discard(ws)


async def watch_blueprint(
    path: Path,
    broadcaster: _WsBroadcaster,
    stop_event: asyncio.Event,
    debounce_ms: int = 400,
    own_write_hash: Callable[[], str | None] = lambda: None,
) -> None:
    """Push a `file_changed` event whenever the blueprint changes on disk.

    Watches the parent directory (editors save via atomic rename, which would
    drop a watch on the file itself) and filters to the blueprint path.

    Saves made through the editor API broadcast their own event, so the disk
    echo of those writes is suppressed: when the changed file's content hash
    matches the last API write, the event is dropped. Suppressing by content
    (rather than consuming a one-shot flag) also absorbs duplicate filesystem
    events for a single write.
    """
    resolved = path.resolve()
    async for changes in awatch(path.parent, stop_event=stop_event, debounce=debounce_ms):
        if not any(Path(changed).resolve() == resolved for _, changed in changes):
            continue
        if _is_own_write_echo(path, own_write_hash):
            continue
        await broadcaster.broadcast(
            {"type": "file_changed", "path": str(path), "origin": "disk"}
        )


def _is_own_write_echo(path: Path, own_write_hash: Callable[[], str | None]) -> bool:
    """True when a change event is just the disk echo of our own API write."""
    try:
        current = _content_hash(path.read_text(encoding="utf-8"))
    except OSError:
        return False  # deleted/unreadable — let the UI refetch and surface it
    return current == own_write_hash()


def create_app(
    blueprint_path: Path,
    token: str | None = None,
    static_dir: Path | None = None,
    watch_debounce_ms: int = 400,
) -> FastAPI:
    """Build the editor app: token guard, /api endpoints, /ws, embedded UI mount."""
    broadcaster = _WsBroadcaster()
    last_write: dict[str, str | None] = {"hash": None}
    loop_box: dict[str, asyncio.AbstractEventLoop | None] = {"loop": None}

    def publish_from_worker(message: dict[str, Any]) -> None:
        """Thread-safe bridge: task worker threads → WS broadcast on the loop.

        Without a captured loop (lifespan-less TestClient) events are dropped;
        the task record still accumulates them for GET /api/tasks/current.
        """
        loop = loop_box["loop"]
        if loop is None or loop.is_closed():
            return

        def _send() -> None:
            asyncio.ensure_future(broadcaster.broadcast(message))

        loop.call_soon_threadsafe(_send)

    tasks = TaskManager(blueprint_path, publish_from_worker)
    chat = ChatSession(blueprint_path, publish_from_worker)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        loop_box["loop"] = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        watcher = asyncio.create_task(
            watch_blueprint(
                blueprint_path,
                broadcaster,
                stop_event,
                watch_debounce_ms,
                own_write_hash=lambda: last_write["hash"],
            )
        )
        yield
        loop_box["loop"] = None
        chat.stop()  # don't leave a generated-project process orphaned
        stop_event.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    app = FastAPI(title="ABP Editor", docs_url=None, redoc_url=None, lifespan=lifespan)
    static = STATIC_DIR if static_dir is None else static_dir

    if token is not None:
        expected = token

        @app.middleware("http")
        async def token_guard(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            supplied = request.query_params.get("token") or request.cookies.get(TOKEN_COOKIE)
            if supplied is None or not secrets.compare_digest(supplied, expected):
                return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
            response = await call_next(request)
            if request.cookies.get(TOKEN_COOKIE) != expected:
                # First visit arrives with ?token=... — persist it so in-app
                # fetches (no query param) pass the guard.
                response.set_cookie(TOKEN_COOKIE, expected, httponly=True, samesite="strict")
            return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": _abp_version()}

    @app.get("/api/blueprint")
    def blueprint() -> dict[str, Any]:
        return blueprint_info(blueprint_path)

    @app.get("/api/schema")
    def schema() -> dict[str, Any]:
        """The blueprint JSON Schema (same as `abp schema`) — drives config forms."""
        return BlueprintSpec.model_json_schema()

    @app.put("/api/blueprint/yaml")
    async def save_yaml(body: YamlSaveRequest) -> Any:
        """Whole-file save from the source pane (last-writer-wins)."""
        syntax_error = yaml_syntax_error(body.yaml)
        if syntax_error is not None:
            return JSONResponse({"detail": syntax_error}, status_code=422)
        blueprint_path.write_text(body.yaml, encoding="utf-8")
        last_write["hash"] = _content_hash(body.yaml)
        # Other connected tabs sync from this push; the watcher suppresses the
        # disk echo so each save produces exactly one event.
        await broadcaster.broadcast(
            {"type": "file_changed", "path": str(blueprint_path), "origin": "save"}
        )
        return blueprint_info(blueprint_path)

    @app.post("/api/blueprint/ops")
    async def blueprint_ops(body: OpsRequest) -> Any:
        """Canvas ops: targeted ruamel mutations, strict validate-before-write.

        Unlike whole-file saves (where the user owns the text), a canvas op
        producing an invalid blueprint is rejected and nothing is written.
        """
        current_text = blueprint_path.read_text(encoding="utf-8")
        if _content_hash(current_text) != body.base_hash:
            return JSONResponse({"detail": "file changed underneath"}, status_code=409)
        try:
            document = load_blueprint_document(blueprint_path)
            apply_ops(document, body.ops)
            resolved = resolve_blueprint_data(document, blueprint_path=blueprint_path)
            BlueprintSpec.model_validate(resolved)
        except (OpError, BlueprintValidationError, ValidationError) as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        buffer = io.StringIO()
        _ruamel_yaml.dump(document, buffer)
        text = buffer.getvalue()
        blueprint_path.write_text(text, encoding="utf-8")
        last_write["hash"] = _content_hash(text)
        await broadcaster.broadcast(
            {"type": "file_changed", "path": str(blueprint_path), "origin": "save"}
        )
        return blueprint_info(blueprint_path)

    @app.post("/api/expression/validate")
    def validate_expression(body: ExpressionRequest) -> dict[str, Any]:
        """Live edge-condition check for the canvas (read-only, no write)."""
        return check_expression(body.expression)

    @app.post("/api/actions/{action}")
    def start_action(action: str, body: ActionRequest) -> Any:
        """Start a background action task; progress streams over /ws."""
        try:
            record = tasks.start(action, body.params)
        except TaskBusyError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except ActionError as e:  # unknown action name
            return JSONResponse({"detail": str(e)}, status_code=404)
        return {"task": record.to_dict()}

    @app.get("/api/tasks/current")
    def current_task() -> dict[str, Any]:
        """The running (or most recently finished) task — lets a reloaded tab resync."""
        record = tasks.current
        return {"task": record.to_dict() if record is not None else None}

    @app.post("/api/tasks/current/cancel")
    def cancel_task() -> dict[str, bool]:
        return {"cancelled": tasks.cancel()}

    @app.get("/api/chat")
    def chat_state() -> dict[str, Any]:
        """Current chat session (status, thread_id, history) — lets a tab resync."""
        return {"chat": chat.snapshot()}

    @app.post("/api/chat/start")
    def chat_start(body: ChatStartRequest) -> dict[str, Any]:
        """(Re)start a persistent chat session with a fresh thread_id."""
        return {"chat": chat.start(install=body.install)}

    @app.post("/api/chat/send")
    def chat_send(body: ChatSendRequest) -> Any:
        """Send a user message; the agent's reply arrives over /ws."""
        if not body.message.strip():
            return JSONResponse({"detail": "message must not be empty"}, status_code=422)
        try:
            chat.send(body.message)
        except ChatError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        return {"chat": chat.snapshot()}

    @app.post("/api/chat/stop")
    def chat_stop() -> dict[str, Any]:
        return {"chat": chat.stop()}

    @app.put("/api/layout")
    def save_layout(body: LayoutSaveRequest) -> dict[str, bool]:
        layout_store.save_layout(
            blueprint_path,
            {node_id: {"x": pos.x, "y": pos.y} for node_id, pos in body.positions.items()},
        )
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        # The token middleware only covers HTTP scopes — re-check here. The
        # browser carries the cookie set by the first page load.
        if token is not None:
            supplied = websocket.query_params.get("token") or websocket.cookies.get(TOKEN_COOKIE)
            if supplied is None or not secrets.compare_digest(supplied, token):
                await websocket.close(code=1008)
                return
        await websocket.accept()
        broadcaster.connections.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # ignore client messages; push-only
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.connections.discard(websocket)

    if (static / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    else:

        @app.get("/")
        def missing_ui() -> JSONResponse:
            return JSONResponse({"detail": _MISSING_UI_HINT}, status_code=503)

    return app


def pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def run_editor(
    blueprint_path: Path,
    *,
    port: int | None = None,
    open_browser: bool = True,
    dev: bool = False,
    url_callback: Callable[[str], None] | None = None,
) -> None:
    """Start the editor server on 127.0.0.1 and block until interrupted.

    Dev mode (`abp editor --dev`) serves the API only, on a fixed port with no
    token, for use behind the Vite dev-server proxy.
    """
    host = "127.0.0.1"
    token = None if dev else secrets.token_urlsafe(16)
    resolved_port = port if port is not None else (DEV_PORT if dev else pick_free_port(host))
    app = create_app(blueprint_path, token=token)

    url = f"http://{host}:{resolved_port}/"
    if token is not None:
        url += f"?token={token}"
    if url_callback is not None:
        url_callback(url)

    browser_timer: threading.Timer | None = None
    if open_browser and not dev:
        # Give uvicorn a beat to bind before the browser fires its request.
        browser_timer = threading.Timer(0.5, webbrowser.open, args=(url,))
        browser_timer.start()
    try:
        uvicorn.run(app, host=host, port=resolved_port, log_level="warning")
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
