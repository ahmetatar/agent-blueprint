"""Tests for the editor's persistent chat session (phase E5.1).

These drive the *real* JSON-lines driver (`session._CHAT_DRIVER`) against a
fake `main.py` so no langgraph/langchain install is needed — the bridge,
multi-turn persistence, error surfacing, and lifecycle are all exercised
without an LLM.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agent_blueprint.editor.session import ChatError, ChatSession

_BLUEPRINT = """\
blueprint:
  name: "chat-test"
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

# A fake generated project: `run` keeps process-local state so a passing
# multi-turn test proves the SAME process handled both turns (the whole point
# of a persistent session).
_FAKE_MAIN = """\
_TURNS = []


def run(user_input, thread_id="default"):
    _TURNS.append(user_input)
    return "turn %d:%s" % (len(_TURNS), user_input)
"""

_FAKE_MAIN_RAISES = """\
def run(user_input, thread_id="default"):
    raise RuntimeError("boom: %s" % user_input)
"""

_FAKE_MAIN_IMPORT_ERROR = "raise ImportError('missing dep')\n"


class _Collector:
    """Thread-safe sink for published WS events with a wait helper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []

    def __call__(self, message: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(message)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)

    def wait_for(self, predicate: Any, timeout: float = 20.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.snapshot():
                if predicate(event):
                    return event
            time.sleep(0.02)
        raise AssertionError(f"no event matched within {timeout}s; saw {self.snapshot()}")


def _materialize_factory(main_source: str):
    """A fake `_materialize_project` that writes `main_source` and skips install."""

    def fake(self: ChatSession, *, install: bool = True) -> Path:
        tempdir = Path(tempfile.mkdtemp(prefix="abp_chat_test_"))
        (tempdir / "main.py").write_text(main_source, encoding="utf-8")
        return tempdir

    return fake


def _blueprint(tmp_path: Path) -> Path:
    path = tmp_path / "bp.yml"
    path.write_text(_BLUEPRINT, encoding="utf-8")
    return path


def _wait_ready(collector: _Collector, timeout: float = 20.0) -> dict[str, Any]:
    return collector.wait_for(
        lambda e: e.get("type") == "chat_status" and e.get("status") == "ready", timeout
    )


def test_chat_session_multi_turn_same_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN))
    collector = _Collector()
    session = ChatSession(_blueprint(tmp_path), collector)
    try:
        session.start(install=False)
        ready = _wait_ready(collector)
        assert ready["thread_id"].startswith("editor-")

        session.send("hi")
        first = collector.wait_for(
            lambda e: e.get("type") == "chat_message" and e["message"]["role"] == "agent"
        )
        assert first["message"]["content"] == "turn 1:hi"

        session.send("again")
        # The second reply is "turn 2:..." only if the SAME process kept state.
        collector.wait_for(
            lambda e: e.get("type") == "chat_message"
            and e["message"]["role"] == "agent"
            and e["message"]["content"] == "turn 2:again"
        )

        history = session.snapshot()["history"]
        roles = [(m["role"], m["content"]) for m in history]
        assert roles == [
            ("user", "hi"),
            ("agent", "turn 1:hi"),
            ("user", "again"),
            ("agent", "turn 2:again"),
        ]
    finally:
        session.stop()


def test_chat_session_surfaces_run_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN_RAISES)
    )
    collector = _Collector()
    session = ChatSession(_blueprint(tmp_path), collector)
    try:
        session.start(install=False)
        _wait_ready(collector)
        session.send("hi")
        err = collector.wait_for(
            lambda e: e.get("type") == "chat_message" and e["message"]["role"] == "error"
        )
        assert "boom: hi" in err["message"]["content"]
        # A runtime error keeps the session alive (still ready for the next turn).
        assert session.snapshot()["status"] == "ready"
    finally:
        session.stop()


def test_chat_session_import_failure_becomes_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN_IMPORT_ERROR)
    )
    collector = _Collector()
    session = ChatSession(_blueprint(tmp_path), collector)
    try:
        session.start(install=False)
        collector.wait_for(
            lambda e: e.get("type") == "chat_status" and e.get("status") == "error",
            timeout=20.0,
        )
        assert "failed to import generated project" in (session.snapshot()["error"] or "")
    finally:
        session.stop()


def test_send_before_ready_raises(tmp_path: Path) -> None:
    session = ChatSession(_blueprint(tmp_path), _Collector())
    with pytest.raises(ChatError, match="start one first"):
        session.send("hi")


def test_invalid_blueprint_yields_error_status(tmp_path: Path) -> None:
    path = tmp_path / "bp.yml"
    path.write_text("blueprint:\n  version: '1.0'\n", encoding="utf-8")  # missing name
    collector = _Collector()
    session = ChatSession(path, collector)
    session.start(install=False)
    collector.wait_for(
        lambda e: e.get("type") == "chat_status" and e.get("status") == "error"
    )
    assert "does not validate" in (session.snapshot()["error"] or "")


def test_new_session_changes_thread_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN))
    collector = _Collector()
    session = ChatSession(_blueprint(tmp_path), collector)
    try:
        session.start(install=False)
        first = _wait_ready(collector)["thread_id"]
        session.start(install=False)  # "New session"
        second = collector.wait_for(
            lambda e: e.get("type") == "chat_status"
            and e.get("status") == "ready"
            and e.get("thread_id") != first
        )["thread_id"]
        assert second != first
        # History is reset for the new session.
        assert session.snapshot()["history"] == []
    finally:
        session.stop()


def test_resume_thread_reloads_persisted_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN))
    bp = _blueprint(tmp_path)
    collector = _Collector()
    session = ChatSession(bp, collector)
    try:
        session.start(install=False)
        thread_id = _wait_ready(collector)["thread_id"]
        session.send("hi")
        collector.wait_for(
            lambda e: e.get("type") == "chat_message" and e["message"]["role"] == "agent"
        )
        session.stop()

        # A brand-new session object (as if the editor restarted) resumes the
        # same thread and gets the persisted transcript back.
        collector2 = _Collector()
        session2 = ChatSession(bp, collector2)
        session2.start(install=False, thread_id=thread_id)
        _wait_ready(collector2)
        history = session2.snapshot()["history"]
        assert [(m["role"], m["content"]) for m in history][:2] == [
            ("user", "hi"),
            ("agent", "turn 1:hi"),
        ]
        assert session2.snapshot()["thread_id"] == thread_id
        session2.stop()
    finally:
        session.stop()


def test_delete_active_thread_stops_and_forgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN))
    bp = _blueprint(tmp_path)
    collector = _Collector()
    session = ChatSession(bp, collector)
    session.start(install=False)
    thread_id = _wait_ready(collector)["thread_id"]
    session.send("hi")
    collector.wait_for(
        lambda e: e.get("type") == "chat_message" and e["message"]["role"] == "agent"
    )
    assert any(t["thread_id"] == thread_id for t in session.list_threads())

    session.delete_thread(thread_id)
    assert session.snapshot()["status"] == "stopped"  # active thread was stopped
    assert all(t["thread_id"] != thread_id for t in session.list_threads())


def test_stop_marks_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ChatSession, "_materialize_project", _materialize_factory(_FAKE_MAIN))
    collector = _Collector()
    session = ChatSession(_blueprint(tmp_path), collector)
    session.start(install=False)
    _wait_ready(collector)
    snap = session.stop()
    assert snap["status"] == "stopped"
    # Stopping must not have published a spurious "error" status for the exit.
    time.sleep(0.2)
    assert not any(
        e.get("type") == "chat_status" and e.get("status") == "error"
        for e in collector.snapshot()
    )
