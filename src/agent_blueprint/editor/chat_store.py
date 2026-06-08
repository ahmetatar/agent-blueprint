"""Editor-private durable chat persistence (phase E5.5).

Keeps chat state next to the blueprint under `.abp/chat/`:

- ``<stem>.db`` — the LangGraph SQLite checkpointer (the agent's own state;
  durable across editor restarts via ``ABP_CHECKPOINT_DB``).
- ``<stem>.threads.json`` — a lightweight index for the thread browser: per
  thread, the display transcript and a preview. This is editor-only metadata
  (like the layout sidecar) — never required, safe to delete; the checkpoint
  db is the source of truth for *continuing* a conversation.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def chat_dir(blueprint_path: Path) -> Path:
    return blueprint_path.parent / ".abp" / "chat"


def db_path(blueprint_path: Path) -> Path:
    return chat_dir(blueprint_path) / f"{blueprint_path.stem}.db"


def _index_path(blueprint_path: Path) -> Path:
    return chat_dir(blueprint_path) / f"{blueprint_path.stem}.threads.json"


def load_index(blueprint_path: Path) -> dict[str, Any]:
    path = _index_path(blueprint_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # corrupt index → start clean, like the layout sidecar
    return data if isinstance(data, dict) else {}


def _write_index(blueprint_path: Path, index: dict[str, Any]) -> None:
    chat_dir(blueprint_path).mkdir(parents=True, exist_ok=True)
    path = _index_path(blueprint_path)
    # Atomic write so a concurrent reader never sees a half-written file
    # (corrupt JSON would otherwise read as an empty index).
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def list_threads(blueprint_path: Path) -> list[dict[str, Any]]:
    """Threads for the browser, most-recently-updated first."""
    threads: list[dict[str, Any]] = []
    for thread_id, entry in load_index(blueprint_path).items():
        if not isinstance(entry, dict):
            continue
        raw_history = entry.get("history")
        history = raw_history if isinstance(raw_history, list) else []
        threads.append(
            {
                "thread_id": thread_id,
                "updated": entry.get("updated"),
                "count": len(history),
                "preview": entry.get("preview", ""),
            }
        )
    threads.sort(key=lambda t: t.get("updated") or "", reverse=True)
    return threads


def load_history(blueprint_path: Path, thread_id: str) -> list[dict[str, str]]:
    entry = load_index(blueprint_path).get(thread_id)
    if isinstance(entry, dict) and isinstance(entry.get("history"), list):
        return [m for m in entry["history"] if isinstance(m, dict)]
    return []


def save_history(
    blueprint_path: Path, thread_id: str, history: list[dict[str, str]]
) -> None:
    index = load_index(blueprint_path)
    preview = next(
        (m.get("content", "")[:80] for m in reversed(history) if m.get("role") == "user"),
        "",
    )
    index[thread_id] = {
        "updated": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "preview": preview,
        "history": history,
    }
    _write_index(blueprint_path, index)


def delete_thread(blueprint_path: Path, thread_id: str) -> None:
    """Forget a thread: drop its index entry and its durable checkpoint rows."""
    index = load_index(blueprint_path)
    if thread_id in index:
        del index[thread_id]
        _write_index(blueprint_path, index)

    db = db_path(blueprint_path)
    if not db.exists():
        return
    # Best-effort, schema-robust: delete from every table that keys on a
    # thread_id (the langgraph sqlite schema has varied across versions).
    with contextlib.suppress(sqlite3.Error):
        conn = sqlite3.connect(str(db))
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
            for table in tables:
                cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
                if "thread_id" in cols:
                    conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = ?",  # noqa: S608 - table from sqlite_master, not user input
                        (thread_id,),
                    )
            conn.commit()
        finally:
            conn.close()
