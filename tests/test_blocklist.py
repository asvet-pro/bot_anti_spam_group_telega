"""Тесты стоп-листа: границы слов вместо подстрочного поиска."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bot.db import Database
from bot.filters.blocklist import BlocklistFilter


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "blocklist_test.db"


@pytest.mark.asyncio
async def test_word_matches_whole_word(db_path: Path) -> None:
    """Слово ловится как отдельное слово (в любом регистре)."""
    db = Database(db_path)
    await db.init()
    await db.add_word("mlm")
    bl = BlocklistFilter(db=db)
    assert await bl.match_word("Это схема MLM") == "mlm"
    assert await bl.match_word("mlm") == "mlm"
    # Дефис — не буква, поэтому «mlm-курс» считается отдельным словом
    assert await bl.match_word("mlm-курс") == "mlm"


@pytest.mark.asyncio
async def test_cyrillic_and_latin_are_different(db_path: Path) -> None:
    """Кириллические «МЛМ» и латинские «mlm» — разные слова.

    Это осознанное поведение: стоп-лист «mlm» не должен резать
    кириллическое «МЛМ» (например, аббревиатуру). Если нужно обе
    формы — добавь в стоп-лист обе записи.
    """
    db = Database(db_path)
    await db.init()
    await db.add_word("mlm")
    bl = BlocklistFilter(db=db)
    assert await bl.match_word("Это схема МЛМ") is None
    assert await bl.match_word("Это схема MLM") == "mlm"


@pytest.mark.asyncio
async def test_word_does_not_match_inside_other_word(db_path: Path) -> None:
    """Слово НЕ ловится внутри другого слова (регресс)."""
    db = Database(db_path)
    await db.init()
    await db.add_word("mlm")
    bl = BlocklistFilter(db=db)
    # «млмщик» — другое слово, не должно матчиться
    assert await bl.match_word("млмщик продаёт курсы") is None
    assert await bl.match_word("supermlm") is None


@pytest.mark.asyncio
async def test_word_with_special_chars_matched_as_substring(db_path: Path) -> None:
    """Слово со спецсимволами ищется подстрокой."""
    db = Database(db_path)
    await db.init()
    await db.add_word("c++")
    bl = BlocklistFilter(db=db)
    assert await bl.match_word("пишем на c++ каждый день") == "c++"


@pytest.mark.asyncio
async def test_cyrillic_word_boundaries(db_path: Path) -> None:
    """Русские слова — тоже по границам."""
    db = Database(db_path)
    await db.init()
    await db.add_word("заработок")
    bl = BlocklistFilter(db=db)
    assert await bl.match_word("пассивный заработок найден") == "заработок"
    assert await bl.match_word("заработках и заработках") is None


@pytest.mark.asyncio
async def test_pattern_matching_still_works(db_path: Path) -> None:
    """Regex-паттерны работают как раньше."""
    db = Database(db_path)
    await db.init()
    await db.add_pattern("(?i)крипт")
    bl = BlocklistFilter(db=db)
    hit = await bl.match_pattern("Купил крипту")
    assert hit is not None
    assert hit.pattern == "(?i)крипт"
