"""Тесты разметки всей серии одинаковых сообщений."""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.db import Database


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "series_test.db"


@pytest.mark.asyncio
async def test_find_series_returns_all_same_text_messages(db_path: Path) -> None:
    """Серия — все сообщения одного автора с одинаковым текстом."""
    db = Database(db_path)
    await db.init()

    text = "Заработок на нейросетях, пиши в лс"
    for msg_id in (10, 11, 12):
        await db.add_pending_review(msg_id, 100, -100, text)
        await db.mark_notified(100, -100, text, notice_id=1)

    series = await db.find_series(10)
    assert len(series) == 3
    assert {s["msg_id"] for s in series} == {10, 11, 12}


@pytest.mark.asyncio
async def test_find_series_excludes_other_authors(db_path: Path) -> None:
    """Серия одного автора не включает сообщения другого."""
    db = Database(db_path)
    await db.init()

    text = "Заработок на нейросетях"
    await db.add_pending_review(10, 100, -100, text)
    await db.mark_notified(100, -100, text)
    await db.add_pending_review(20, 200, -100, text)
    await db.mark_notified(200, -100, text)

    series = await db.find_series(10)
    assert len(series) == 1
    assert series[0]["user_id"] == 100


@pytest.mark.asyncio
async def test_find_series_unknown_msg_returns_empty(db_path: Path) -> None:
    """Несуществующий msg_id → пустой список, без исключения."""
    db = Database(db_path)
    await db.init()
    assert await db.find_series(999) == []
