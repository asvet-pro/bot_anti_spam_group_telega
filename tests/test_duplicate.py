"""Тесты детектора дубликатов (одинаковые сообщения подряд)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.db import Database
from bot.filters.duplicate import DuplicateCheck, normalize_text, text_hash


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "dup_test.db"


@pytest.mark.asyncio
async def test_repeated_message_is_detected(db_path: Path) -> None:
    """Третье одинаковое сообщение подряд — дубликат."""
    db = Database(db_path)
    await db.init()
    check = DuplicateCheck(db=db, max_repeats=2, window_seconds=600)

    text = "Продаю курсы по заработку на нейросетях, пиши в лс"
    assert await check.is_duplicate(1, -100, text) == (False, 1)
    assert await check.is_duplicate(1, -100, text) == (False, 2)
    is_dup, count = await check.is_duplicate(1, -100, text)
    assert is_dup is True
    assert count == 3


@pytest.mark.asyncio
async def test_different_messages_not_flagged(db_path: Path) -> None:
    """Разные сообщения подряд — не дубликаты (обычный разговор)."""
    db = Database(db_path)
    await db.init()
    check = DuplicateCheck(db=db, max_repeats=2, window_seconds=600)

    for i in range(6):
        is_dup, _ = await check.is_duplicate(
            1, -100, f"Сообщение номер {i} про разные темы"
        )
        assert is_dup is False, f"сообщение {i} не должно считаться дублем"


@pytest.mark.asyncio
async def test_short_messages_ignored(db_path: Path) -> None:
    """Короткие реплики («да», «ок») не считаются дубликатами."""
    db = Database(db_path)
    await db.init()
    check = DuplicateCheck(db=db, max_repeats=2, window_seconds=600)

    for _ in range(10):
        is_dup, count = await check.is_duplicate(1, -100, "да")
        assert is_dup is False
        assert count == 0


@pytest.mark.asyncio
async def test_duplicates_are_per_user(db_path: Path) -> None:
    """У разных авторов одинаковый текст — не дубль."""
    db = Database(db_path)
    await db.init()
    check = DuplicateCheck(db=db, max_repeats=2, window_seconds=600)

    text = "Одинаковое рекламное сообщение у разных людей"
    for user_id in (1, 2, 3):
        for _ in range(4):
            is_dup, _ = await check.is_duplicate(user_id, -100, text)
    # У каждого автора 4-е сообщение — дубль только у первого,
    # но у остальных оно тоже дубль (тот же текст 3 раза подряд).
    is_dup, count = await check.is_duplicate(1, -100, text)
    assert is_dup is True


@pytest.mark.asyncio
async def test_duplicates_are_per_chat(db_path: Path) -> None:
    """В разных чатах одинаковый текст — не дубль (если chat_id разный)."""
    db = Database(db_path)
    await db.init()
    check = DuplicateCheck(db=db, max_repeats=2, window_seconds=600)

    text = "Одинаковый текст в разных чатах подряд"
    for _ in range(4):
        await check.is_duplicate(1, -100, text)
    is_dup, _ = await check.is_duplicate(1, -200, text)
    assert is_dup is False


def test_normalization_ignores_case_and_spaces() -> None:
    """Регистр и пробелы не влияют на сравнение."""
    assert normalize_text("Привет  Мир") == normalize_text("приветмир")
    assert text_hash("ПРИВЕТ МИР") == text_hash("привет  мир")


def test_normalization_keeps_different_texts_different() -> None:
    """Разные тексты дают разные хэши."""
    assert text_hash("реклама услуг") != text_hash("реклама товаров")
