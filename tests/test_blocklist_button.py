"""Тест: кнопка «В стоп-лист» кладёт правило в правильный список.

Регресс: regex-паттерн (например «(?i)крипт») попадал в список СЛОВ,
где искался как подстрока «(?i)крипт» — и больше никогда ничего не ловил.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.db import Database
from bot.filters.blocklist import BlocklistFilter
from bot.handlers.review import _looks_like_regex


@pytest.mark.asyncio
async def test_regex_goes_to_patterns_not_words(tmp_path: Path) -> None:
    """Regex-паттерн должен попасть в blocklist_patterns."""
    db = Database(tmp_path / "t.db")
    await db.init()

    hint = "(?i)крипт"
    if _looks_like_regex(hint):
        await db.add_pattern(hint, note="из разметки")
    else:
        await db.add_word(hint, note="из разметки")

    words = [w.word for w in await db.list_words()]
    patterns = [p.pattern for p in await db.list_patterns()]

    assert words == [], f"regex не должен попадать в слова: {words}"
    assert patterns == ["(?i)крипт"]

    # И теперь реально ловит.
    bl = BlocklistFilter(db=db)
    assert await bl.match_pattern("Купил крипту") is not None


@pytest.mark.asyncio
async def test_plain_word_goes_to_words(tmp_path: Path) -> None:
    """Обычное слово должно попасть в blocklist_words."""
    db = Database(tmp_path / "t2.db")
    await db.init()

    hint = "казино"
    if _looks_like_regex(hint):
        await db.add_pattern(hint, note="из разметки")
    else:
        await db.add_word(hint, note="из разметки")

    words = [w.word for w in await db.list_words()]
    patterns = [p.pattern for p in await db.list_patterns()]

    assert words == ["казино"]
    assert patterns == []

    bl = BlocklistFilter(db=db)
    assert await bl.match_word("играл в казино") == "казино"


def test_regex_detection() -> None:
    """Определение «похоже на regex»."""
    assert _looks_like_regex("(?i)крипт") is True
    assert _looks_like_regex("(?i)заработ\\w*") is True
    assert _looks_like_regex("http(s)?://(?!t\\.me/x)") is True
    assert _looks_like_regex("вакансия.*нейросет") is True

    assert _looks_like_regex("казино") is False
    assert _looks_like_regex("заработок") is False
    assert _looks_like_regex("") is False
