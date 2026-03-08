"""FastAPI server for the Agent Blueprint visual editor."""

import copy
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ruamel.yaml import YAML

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_blueprint() -> dict[str, Any]:
    return {
        "blueprint": {"name": "my-agent", "version": "1.0", "description": ""},
        "settings": {"default_model": "gpt-4o", "default_temperature": 0.7},
        "state": {"fields": {}},
        "agents": {},
        "tools": {},
        "graph": {"entry_point": "", "nodes": {}, "edges": []},
        "memory": {"backend": "in_memory"},
    }


def _to_yaml_string(data: dict[str, Any]) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 4096
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def _strip_ui_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Remove _ui_* keys before serialising to YAML."""
    data = copy.deepcopy(data)
    nodes = (data.get("graph") or {}).get("nodes") or {}
    for node in nodes.values():
        for k in list(node.keys()):
            if k.startswith("_ui_"):
                del node[k]
    return data


def _clean_for_yaml(bp: dict[str, Any]) -> dict[str, Any]:
    """Return a blueprint dict ready for YAML output (no UI metadata)."""
    clean = _strip_ui_keys(bp)
    # Remove empty top-level sections that would clutter the YAML
    for key in ("tools", "model_providers", "mcp_servers"):
        if key in clean and not clean[key]:
            del clean[key]
    return clean


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(blueprint_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Agent Blueprint UI", docs_url=None, redoc_url=None)

    state: dict[str, Any] = {
        "blueprint": _default_blueprint(),
        "ui_positions": {},  # nodeId -> {x, y}
        "source_path": None,
    }

    if blueprint_path and blueprint_path.exists():
        from agent_blueprint.utils.yaml_loader import load_blueprint_yaml
        state["blueprint"] = load_blueprint_yaml(blueprint_path)
        state["source_path"] = str(blueprint_path.resolve())

    # ------------------------------------------------------------------
    # Static
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # State API
    # ------------------------------------------------------------------

    @app.get("/api/state")
    async def get_state() -> dict:
        return {
            "blueprint": state["blueprint"],
            "ui_positions": state["ui_positions"],
            "source_path": state["source_path"],
        }

    @app.put("/api/state")
    async def put_state(request: Request) -> dict:
        body = await request.json()
        if "blueprint" in body:
            state["blueprint"] = body["blueprint"]
        if "ui_positions" in body:
            state["ui_positions"] = body["ui_positions"]
        return {"ok": True}

    # ------------------------------------------------------------------
    # YAML generation
    # ------------------------------------------------------------------

    @app.post("/api/yaml")
    async def to_yaml(request: Request) -> dict:
        body = await request.json()
        bp = body.get("blueprint", state["blueprint"])
        return {"yaml": _to_yaml_string(_clean_for_yaml(bp))}

    # ------------------------------------------------------------------
    # Save file
    # ------------------------------------------------------------------

    @app.post("/api/save")
    async def save_file(request: Request) -> JSONResponse:
        body = await request.json()
        path = body.get("path") or state["source_path"]
        if not path:
            return JSONResponse({"error": "No file path — use Save As"}, status_code=400)
        bp = body.get("blueprint", state["blueprint"])
        Path(path).write_text(_to_yaml_string(_clean_for_yaml(bp)), encoding="utf-8")
        state["source_path"] = path
        return JSONResponse({"ok": True, "path": path})

    # ------------------------------------------------------------------
    # Run agent
    # ------------------------------------------------------------------

    @app.post("/api/run")
    async def run_agent(request: Request) -> dict:
        body = await request.json()
        bp = body.get("blueprint", state["blueprint"])
        user_input = body.get("input", "Hello!")

        yaml_str = _to_yaml_string(_clean_for_yaml(bp))
        with tempfile.NamedTemporaryFile(
            suffix=".yml", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_str)
            tmp = f.name

        try:
            # Validate first so we give a nice error
            from pydantic import ValidationError
            from agent_blueprint.models.blueprint import BlueprintSpec
            from agent_blueprint.utils.yaml_loader import load_blueprint_yaml
            try:
                raw = load_blueprint_yaml(Path(tmp))
                BlueprintSpec.model_validate(raw)
            except (ValidationError, Exception) as e:
                return {"stdout": "", "stderr": str(e), "returncode": 1}

            result = subprocess.run(
                ["abp", "run", tmp, user_input, "--no-install"],
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ},
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Timeout after 120s", "returncode": -1}
        except FileNotFoundError:
            # abp not in PATH — try via python -m
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "agent_blueprint.cli.app", "run",
                     tmp, user_input, "--no-install"],
                    capture_output=True, text=True, timeout=120,
                    env={**os.environ},
                )
                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                }
            except Exception as e:
                return {"stdout": "", "stderr": str(e), "returncode": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "returncode": -1}
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    return app
