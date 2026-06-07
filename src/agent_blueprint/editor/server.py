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
from pydantic import ValidationError
from watchfiles import awatch

from agent_blueprint.editor.diagnostics import lint_with_positions
from agent_blueprint.editor.viewmodel import build_view_model
from agent_blueprint.exceptions import BlueprintValidationError
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

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
    """Raw YAML, validation status, graph view-model, and lint findings."""
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
    }


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
) -> None:
    """Push a `file_changed` event whenever the blueprint changes on disk.

    Watches the parent directory (editors save via atomic rename, which would
    drop a watch on the file itself) and filters to the blueprint path.
    """
    resolved = path.resolve()
    async for changes in awatch(path.parent, stop_event=stop_event, debounce=debounce_ms):
        if any(Path(changed).resolve() == resolved for _, changed in changes):
            await broadcaster.broadcast({"type": "file_changed", "path": str(path)})


def create_app(
    blueprint_path: Path,
    token: str | None = None,
    static_dir: Path | None = None,
    watch_debounce_ms: int = 400,
) -> FastAPI:
    """Build the editor app: token guard, /api endpoints, /ws, embedded UI mount."""
    broadcaster = _WsBroadcaster()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop_event = asyncio.Event()
        watcher = asyncio.create_task(
            watch_blueprint(blueprint_path, broadcaster, stop_event, watch_debounce_ms)
        )
        yield
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
