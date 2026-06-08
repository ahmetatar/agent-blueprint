"""Persistent chat session for the editor (phase E5.1).

The editor's *Run…* action is one-shot: it generates the project, runs it once
in a subprocess, and throws the process away — so the in-memory checkpointer is
discarded after each run and there is no conversation history. A chat session
fixes that by keeping **one** generated-project process alive: the graph (and
its ``MemorySaver``) is built once at import, so calling ``run(msg, thread_id)``
repeatedly with the same thread accumulates history across turns.

Rather than drive the generated ``_abp_runner.py`` REPL and parse its
human-oriented ``You:`` / ``Agent:`` text over a pipe (fragile, and a robust
fix would mean changing the generated template), the editor writes its **own**
tiny JSON-lines driver next to the generated project and runs that. The driver
imports the same ``run`` from ``main.py``; one JSON object per line in each
direction makes the bridge unambiguous. This keeps the whole feature inside
``editor/`` — no template/core change.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_blueprint.exceptions import (
    BlueprintCompilationError,
    BlueprintValidationError,
    GeneratorError,
)
from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

DRIVER_NAME = "_abp_editor_chat.py"

# Editor-owned driver dropped next to the generated project. Reads one JSON
# object per line from stdin ({"input": "..."}) and emits one JSON object per
# line on stdout. Non-JSON stdout (stray library prints) is ignored by the
# reader, so framing stays robust.
_CHAT_DRIVER = '''\
"""Editor chat driver (written by agent-blueprint `abp editor`). DO NOT EDIT."""
import json
import os
import sys


def _emit(obj):
    sys.stdout.write(json.dumps(obj, default=str) + "\\n")
    sys.stdout.flush()


try:
    from main import run
except Exception as exc:  # noqa: BLE001 - report any import failure to the editor
    _emit({"type": "fatal", "error": "failed to import generated project: %s" % exc})
    sys.exit(1)

THREAD_ID = os.environ.get("ABP_THREAD_ID", "editor")
_emit({"type": "ready", "thread_id": THREAD_ID})

while True:
    line = sys.stdin.readline()
    if not line:  # stdin closed — editor is shutting us down
        break
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        continue
    text = message.get("input", "")
    try:
        response = run(text, thread_id=THREAD_ID)
        if not isinstance(response, str):
            response = json.dumps(response, default=str)
        _emit({"type": "agent", "content": response})
    except Exception as exc:  # noqa: BLE001 - surface runtime errors in the chat
        _emit({"type": "error", "content": str(exc)})
'''


class ChatError(Exception):
    """The chat session could not start or accept input."""


class ChatSession:
    """One persistent generated-project process driving a multi-turn chat.

    Lifecycle is single-slot per editor server: :meth:`start` stops any prior
    process and spawns a fresh one with a new ``thread_id``. Heavy work
    (generation + dependency install + spawn) runs on a worker thread so the
    request returns immediately; readiness and replies arrive over the WS.
    """

    def __init__(
        self,
        blueprint_path: Path,
        publish: Callable[[dict[str, Any]], None],
    ) -> None:
        self._blueprint_path = blueprint_path
        self._publish = publish  # thread-safe bridge to the WS broadcaster
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._tempdir: Path | None = None
        self._thread_id: str | None = None
        self._status = "idle"  # idle | starting | ready | error | stopped
        self._error: str | None = None
        self._history: list[dict[str, str]] = []
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._generation = 0  # bumped each (re)start so stale readers no-op

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, *, install: bool = True) -> dict[str, Any]:
        """(Re)start the session: stop any prior process, then spawn fresh.

        Returns the snapshot immediately with ``status="starting"``; the real
        spawn happens on a worker thread and publishes ``chat_status`` when it
        is ready (or errors).
        """
        with self._lock:
            self._teardown_locked()
            self._generation += 1
            generation = self._generation
            self._thread_id = f"editor-{uuid.uuid4().hex[:8]}"
            self._status = "starting"
            self._error = None
            self._history = []
            self._stderr_tail.clear()
        self._publish_status()
        worker = threading.Thread(
            target=self._spawn,
            args=(generation, install),
            daemon=True,
        )
        worker.start()
        return self.snapshot()

    def send(self, message: str) -> None:
        """Forward a user message to the live process (reply arrives over WS)."""
        with self._lock:
            if self._status != "ready" or self._process is None:
                raise ChatError("no chat session is ready — start one first")
            process = self._process
            self._history.append({"role": "user", "content": message})
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"input": message}) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ChatError(f"chat process is not accepting input: {e}") from e
        # Echo the user turn so a reloaded tab and other tabs stay in sync.
        self._publish({"type": "chat_message", "message": {"role": "user", "content": message}})

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._teardown_locked()
            self._status = "stopped"
        self._publish_status()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "thread_id": self._thread_id,
                "error": self._error,
                "history": list(self._history),
            }

    # ------------------------------------------------------------------
    # Worker-thread spawn + readers
    # ------------------------------------------------------------------

    def _spawn(self, generation: int, install: bool) -> None:
        try:
            tempdir = self._materialize_project(install=install)
        except Exception as e:  # noqa: BLE001 - report any prep failure as status=error
            with self._lock:
                if generation != self._generation:
                    return
                self._status = "error"
                self._error = str(e)
            self._publish_status()
            return

        (tempdir / DRIVER_NAME).write_text(_CHAT_DRIVER, encoding="utf-8")
        env = self._build_env(tempdir)
        # -u + PYTHONUNBUFFERED so replies flush to us without a pipe deadlock.
        process = subprocess.Popen(
            [sys.executable, "-u", DRIVER_NAME],
            cwd=str(tempdir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        with self._lock:
            if generation != self._generation:
                # A newer start() superseded us while we were preparing.
                process.kill()
                return
            self._tempdir = tempdir
            self._process = process
        threading.Thread(
            target=self._read_stdout, args=(generation, process), daemon=True
        ).start()
        threading.Thread(
            target=self._read_stderr, args=(generation, process), daemon=True
        ).start()

    def _read_stdout(self, generation: int, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if generation != self._generation:
                return
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray library print — not part of our protocol
            self._handle_event(generation, event)
        # stdout closed → the process exited.
        self._handle_exit(generation, process)

    def _read_stderr(self, generation: int, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if generation != self._generation:
                return
            self._stderr_tail.append(line.rstrip("\n"))

    def _handle_event(self, generation: int, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "ready":
            with self._lock:
                if generation != self._generation:
                    return
                self._status = "ready"
            self._publish_status()
            return
        if kind == "fatal":
            with self._lock:
                if generation != self._generation:
                    return
                self._status = "error"
                self._error = str(event.get("error", "chat process failed to start"))
            self._publish_status()
            return
        if kind in ("agent", "error"):
            role = "agent" if kind == "agent" else "error"
            content = str(event.get("content", ""))
            with self._lock:
                if generation != self._generation:
                    return
                self._history.append({"role": role, "content": content})
            self._publish({"type": "chat_message", "message": {"role": role, "content": content}})

    def _handle_exit(self, generation: int, process: subprocess.Popen[str]) -> None:
        process.wait()
        with self._lock:
            if generation != self._generation:
                return  # superseded by a newer session — its teardown owns this
            if self._status in ("stopped", "error"):
                return  # we asked it to stop, or already reported a fatal error
            self._status = "error"
            tail = "\n".join(self._stderr_tail).strip()
            self._error = (
                f"chat process exited unexpectedly (code {process.returncode})"
                + (f":\n{tail}" if tail else "")
            )
        self._publish_status()

    # ------------------------------------------------------------------
    # Internal helpers (overridable for tests)
    # ------------------------------------------------------------------

    def _materialize_project(self, *, install: bool) -> Path:
        """Compile + generate the blueprint into a fresh temp dir; install deps.

        Raises :class:`ChatError` on a blueprint that does not validate/compile
        or generate, so the worker reports a clean ``error`` status.
        """
        try:
            spec = BlueprintSpec.model_validate(load_blueprint_yaml(self._blueprint_path))
        except (BlueprintValidationError, ValidationError) as e:
            raise ChatError(f"blueprint does not validate: {e}") from e
        try:
            ir = compile_blueprint(spec)
        except BlueprintCompilationError as e:
            raise ChatError(f"blueprint does not compile: {e}") from e

        tempdir = Path(tempfile.mkdtemp(prefix="abp_chat_"))
        try:
            files = LangGraphGenerator().generate(ir, runner_thread_id=self._thread_id or "editor")
        except GeneratorError as e:
            shutil.rmtree(tempdir, ignore_errors=True)
            raise ChatError(f"generation failed: {e}") from e
        for filename, content in files.items():
            dest = tempdir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        if install:
            req = tempdir / "requirements.txt"
            if req.exists():
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
                    check=False,
                )
        return tempdir

    def _build_env(self, tempdir: Path) -> dict[str, str]:
        env = os.environ.copy()
        env_file = self._blueprint_path.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip())
        # CWD first (so impl: "myapp.x" tools resolve), then the generated dir.
        parts = [p for p in [str(Path.cwd()), str(tempdir), env.get("PYTHONPATH", "")] if p]
        env["PYTHONPATH"] = os.pathsep.join(parts)
        env["PYTHONUNBUFFERED"] = "1"
        env["ABP_THREAD_ID"] = self._thread_id or "editor"
        env.setdefault("ABP_TOOL_APPROVAL_MODE", "deny")
        return env

    def _teardown_locked(self) -> None:
        """Kill the current process and clean its temp dir. Caller holds the lock."""
        if self._process is not None:
            # kill() can race a process that exited a moment ago.
            with contextlib.suppress(Exception):
                self._process.kill()
            self._process = None
        if self._tempdir is not None and self._tempdir.exists():
            shutil.rmtree(self._tempdir, ignore_errors=True)
        self._tempdir = None

    def _publish_status(self) -> None:
        snap = self.snapshot()
        self._publish(
            {
                "type": "chat_status",
                "status": snap["status"],
                "thread_id": snap["thread_id"],
                "error": snap["error"],
            }
        )
