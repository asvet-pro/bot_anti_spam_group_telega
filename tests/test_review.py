"""Тесты мягкого режима: разметка спама админом и работа с БД."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from bot.db import Database


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_bot.db"


@pytest.mark.asyncio
async def test_pending_review_lifecycle(db_path: Path) -> None:
    """Сообщение попадает в очередь, потом размечивается и попадает в отчёты."""
    db = Database(db_path)
    await db.init()

    added = await db.add_pending_review(
        msg_id=42, user_id=100, chat_id=-100,
        text="Заработок на нейросетях, пиши в лс",
        reason="стоп-слово «заработок»",
        blocklist_hint="заработок",
    )
    assert added is True

    # Повторная добавка — не дублирует.
    assert await db.add_pending_review(
        msg_id=42, user_id=100, chat_id=-100, text="x", reason="y"
    ) is False

    pending = await db.get_pending_review(42)
    assert pending is not None
    assert pending["text"] == "Заработок на нейросетях, пиши в лс"

    # Размечаем как спам.
    rec = await db.resolve_pending_review(42, verdict="spam")
    assert rec is not None
    assert rec["verdict"] == "spam"

    # Из очереди исчезло, но осталось в отчётах.
    assert await db.get_pending_review(42) is None
    assert await db.count_reports("spam") == 1
    assert await db.count_reports("ok") == 0

    reports = await db.list_reports(verdict="spam")
    assert len(reports) == 1
    assert reports[0]["user_id"] == 100


@pytest.mark.asyncio
async def test_ok_verdict(db_path: Path) -> None:
    """Вердикт «ок» — сообщение не спам."""
    db = Database(db_path)
    await db.init()
    await db.add_pending_review(7, 200, -100, "Привет, как дела?")
    rec = await db.resolve_pending_review(7, verdict="ok")
    assert rec is not None and rec["verdict"] == "ok"
    assert await db.count_reports("ok") == 1
    assert await db.count_reports("spam") == 0


@pytest.mark.asyncio
async def test_resolve_unknown_returns_none(db_path: Path) -> None:
    """Разметка несуществующего сообщения — None, без исключения."""
    db = Database(db_path)
    await db.init()
    assert await db.resolve_pending_review(999, verdict="spam") is None


@pytest.mark.asyncio
async def test_db_creates_tables_on_existing_file(db_path: Path) -> None:
    """Повторный init на существующей базе не падает (старый bot.db)."""
    db = Database(db_path)
    await db.init()
    await db.init()  # второй раз — без ошибок
    assert db_path.exists()
