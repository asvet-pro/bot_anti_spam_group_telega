"""Тесты: группировка серии уведомлений и снятие бана кнопкой «Ок»."""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.db import Database


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "review_flow_test.db"


@pytest.mark.asyncio
async def test_series_count_increments(db_path: Path) -> None:
    """Серия одинаковых сообщений увеличивает счётчик, а не плодит записи."""
    db = Database(db_path)
    await db.init()

    text = "Заработок на нейросетях, пиши в лс @spam"

    count, notice_id = await db.get_notification(1, -100, text)
    assert count == 0
    assert notice_id is None

    await db.mark_notified(1, -100, text, notice_id=555)

    count, notice_id = await db.get_notification(1, -100, text)
    assert count == 1
    assert notice_id == 555

    await db.bump_notification_count(1, -100, text)
    count, notice_id = await db.get_notification(1, -100, text)
    assert count == 2
    assert notice_id == 555, "id уведомления сохраняется для обновления"


@pytest.mark.asyncio
async def test_series_is_per_author_and_chat(db_path: Path) -> None:
    """Счётчик серии — отдельно для каждого автора и чата."""
    db = Database(db_path)
    await db.init()

    text = "одинаковый текст"
    await db.mark_notified(1, -100, text, notice_id=1)
    await db.bump_notification_count(1, -100, text)

    # Другой автор — свой счётчик
    assert (await db.get_notification(2, -100, text))[0] == 0
    # Другой чат — свой счётчик
    assert (await db.get_notification(1, -200, text))[0] == 0
    # У первого — 2
    assert (await db.get_notification(1, -100, text))[0] == 2


@pytest.mark.asyncio
async def test_mark_notified_resets_count(db_path: Path) -> None:
    """Новое уведомление (другой текст) начинает счётчик с 1."""
    db = Database(db_path)
    await db.init()

    await db.mark_notified(1, -100, "текст А", notice_id=1)
    await db.bump_notification_count(1, -100, "текст А")
    assert (await db.get_notification(1, -100, "текст А"))[0] == 2

    await db.mark_notified(1, -100, "текст Б", notice_id=2)
    assert (await db.get_notification(1, -100, "текст Б"))[0] == 1


@pytest.mark.asyncio
async def test_pending_review_keeps_all_messages_of_series(db_path: Path) -> None:
    """Все сообщения серии остаются в очереди — ни одно не теряется."""
    db = Database(db_path)
    await db.init()

    text = "Заработок на нейросетях"
    for msg_id in range(100, 106):
        await db.add_pending_review(msg_id, 1, -100, text)

    # Каждое сообщение серии — отдельная запись в очереди на разбор.
    for msg_id in range(100, 106):
        assert await db.get_pending_review(msg_id) is not None

    # Разметив одно, остальные не пропадают.
    await db.resolve_pending_review(100, verdict="spam")
    assert await db.get_pending_review(100) is None
    assert await db.get_pending_review(101) is not None
