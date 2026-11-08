"""Тест миграции схемы БД на старой базе.

Критично: на проде БД уже существует, и новый деплой не должен её
уронить. Раньше CREATE INDEX на новую колонку ронял init
(sqlite3.OperationalError: no such column: text_hash).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.db import Database


OLD_SCHEMA = """
CREATE TABLE pending_review (
    msg_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
    text TEXT NOT NULL, reason TEXT, blocklist_hint TEXT, created REAL NOT NULL);
CREATE TABLE notified (
    user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, text_hash TEXT NOT NULL,
    ts REAL NOT NULL, PRIMARY KEY (user_id, chat_id, text_hash));
CREATE TABLE stats (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
INSERT INTO pending_review VALUES (1, 100, -100, 'старый спам', 'r', NULL, 1000.0);
INSERT INTO stats VALUES ('deleted', 5);
"""


@pytest.mark.asyncio
async def test_init_migrates_legacy_database(tmp_path: Path) -> None:
    """init() на старой БД добавляет колонки и сохраняет данные."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    db = Database(db_file)
    await db.init()  # не должно падать

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pending_review)")]
    assert "text_hash" in cols, "колонка text_hash не добавлена"

    cols = [r[1] for r in conn.execute("PRAGMA table_info(notified)")]
    assert "count" in cols
    assert "notice_id" in cols

    rows = conn.execute("SELECT msg_id, text FROM pending_review").fetchall()
    assert rows == [(1, "старый спам")], "старые данные потеряны"

    val = conn.execute("SELECT value FROM stats WHERE key='deleted'").fetchone()
    assert val[0] == 5, "счётчики потеряны"
    conn.close()


@pytest.mark.asyncio
async def test_repeated_init_is_safe(tmp_path: Path) -> None:
    """Повторный init() не падает (бот рестартует при деплое)."""
    db_file = tmp_path / "twice.db"
    db = Database(db_file)
    await db.init()
    await db.init()
    await db.init()
    assert db_file.exists()
