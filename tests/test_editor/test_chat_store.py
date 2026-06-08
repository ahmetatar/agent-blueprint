"""Tests for the editor's durable chat persistence (phase E5.5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_blueprint.editor import chat_store


def _bp(tmp_path: Path) -> Path:
    path = tmp_path / "demo.yml"
    path.write_text("blueprint:\n  name: demo\n", encoding="utf-8")
    return path


def test_paths_live_under_abp_chat(tmp_path: Path) -> None:
    bp = _bp(tmp_path)
    assert chat_store.chat_dir(bp) == tmp_path / ".abp" / "chat"
    assert chat_store.db_path(bp) == tmp_path / ".abp" / "chat" / "demo.db"


def test_save_then_load_round_trips_history(tmp_path: Path) -> None:
    bp = _bp(tmp_path)
    history = [
        {"role": "user", "content": "hi"},
        {"role": "agent", "content": "hello"},
    ]
    chat_store.save_history(bp, "t1", history)
    assert chat_store.load_history(bp, "t1") == history


def test_load_missing_thread_is_empty(tmp_path: Path) -> None:
    assert chat_store.load_history(_bp(tmp_path), "nope") == []


def test_list_threads_sorted_recent_first_with_preview(tmp_path: Path) -> None:
    bp = _bp(tmp_path)
    chat_store.save_history(bp, "t1", [{"role": "user", "content": "first thread"}])
    chat_store.save_history(bp, "t2", [{"role": "user", "content": "second thread"}])
    threads = chat_store.list_threads(bp)
    assert [t["thread_id"] for t in threads] == ["t2", "t1"]  # most recent first
    assert threads[0]["preview"] == "second thread"
    assert threads[0]["count"] == 1


def test_corrupt_index_is_ignored(tmp_path: Path) -> None:
    bp = _bp(tmp_path)
    chat_store.chat_dir(bp).mkdir(parents=True)
    (chat_store.chat_dir(bp) / "demo.threads.json").write_text("{ not json", encoding="utf-8")
    assert chat_store.list_threads(bp) == []


def test_delete_thread_removes_index_and_checkpoint_rows(tmp_path: Path) -> None:
    bp = _bp(tmp_path)
    chat_store.save_history(bp, "t1", [{"role": "user", "content": "x"}])
    chat_store.save_history(bp, "t2", [{"role": "user", "content": "y"}])

    # Stand up a checkpoint db with the langgraph-sqlite shape (thread_id keyed).
    db = chat_store.db_path(bp)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint BLOB)")
    conn.execute("CREATE TABLE writes (thread_id TEXT, val BLOB)")
    conn.executemany("INSERT INTO checkpoints VALUES (?, ?)", [("t1", b"a"), ("t2", b"b")])
    conn.executemany("INSERT INTO writes VALUES (?, ?)", [("t1", b"a"), ("t2", b"b")])
    conn.commit()
    conn.close()

    chat_store.delete_thread(bp, "t1")

    # Index entry gone, the other survives.
    assert [t["thread_id"] for t in chat_store.list_threads(bp)] == ["t2"]
    # t1's rows gone from every thread_id-keyed table; t2 untouched.
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT thread_id FROM checkpoints").fetchall() == [("t2",)]
    assert conn.execute("SELECT thread_id FROM writes").fetchall() == [("t2",)]
    conn.close()


def test_delete_thread_without_db_is_noop(tmp_path: Path) -> None:
    bp = _bp(tmp_path)
    chat_store.save_history(bp, "t1", [{"role": "user", "content": "x"}])
    chat_store.delete_thread(bp, "t1")  # no db file yet — must not raise
    assert chat_store.list_threads(bp) == []
