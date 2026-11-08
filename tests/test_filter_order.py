"""Тесты: порядок фильтров и подбор паттерна для кнопки «В стоп-лист»."""
from __future__ import annotations

import inspect
import re

import bot.handlers.messages as m
from bot.handlers.messages import (
    _word_stem,
    guess_spam_keyword,
    guess_spam_pattern,
)


class TestFilterOrder:
    def test_stoplist_checked_before_ml(self) -> None:
        """Стоп-лист должен проверяться ДО ML.

        Иначе ML перехватывает сообщение раньше стоп-правила, и кнопка
        «В стоп-лист» не появляется (нет suggestion).
        """
        code = inspect.getsource(m.on_message)
        pos_word = code.find("blocklist.match_word")
        pos_regex = code.find("blocklist.match_pattern")
        pos_legacy = code.find("spam.match")
        pos_ml = code.find("ml_spam.is_spam")

        assert -1 not in (pos_word, pos_regex, pos_legacy, pos_ml), \
            "не найдены все блоки проверки"

        assert pos_word < pos_ml, "стоп-слова должны проверяться до ML"
        assert pos_regex < pos_ml, "стоп-regex должны проверяться до ML"
        assert pos_legacy < pos_ml, "BANNED_PATTERNS должны проверяться до ML"


class TestKeywordGuessing:
    def test_picks_brand_in_capitals(self) -> None:
        """Слово с заглавной буквы — вероятный бренд."""
        assert guess_spam_keyword("Курс по Promptium — заработок") == "promptium"

    def test_picks_meaningful_word(self) -> None:
        """Без бренда — самое содержательное слово."""
        assert guess_spam_keyword("Заработок на нейросетях, пиши в личку") == "заработок"

    def test_skips_generic_words(self) -> None:
        """Служебные слова не берутся."""
        assert guess_spam_keyword("Набор в команду") != "набор"
        assert guess_spam_keyword("Помогу заработать") != "помогу"

    def test_empty_text(self) -> None:
        assert guess_spam_keyword("") is None
        assert guess_spam_keyword("да") is None
        assert guess_spam_keyword("ok") is None


class TestWordStem:
    def test_core_is_short_prefix(self) -> None:
        """Ядро — первые 6 символов слова."""
        assert _word_stem("заработком") == "зарабо"
        assert _word_stem("подписками") == "подпис"
        assert _word_stem("promptium") == "prompt"

    def test_short_word_kept_as_is(self) -> None:
        assert _word_stem("код") == "код"


class TestSpamPattern:
    def test_pattern_covers_word_forms(self) -> None:
        """Паттерн должен ловить все словоформы основы."""
        pat = guess_spam_pattern("Заработок на нейросетях, пиши в личку")
        assert pat is not None
        for form in ("заработок", "заработка", "заработке", "заработку"):
            assert re.search(pat, form), f"«{form}» не поймано"

    def test_pattern_case_insensitive(self) -> None:
        pat = guess_spam_pattern("Заработок на нейросетях")
        assert re.search(pat, "ЗАРАБОТОК")

    def test_pattern_is_valid_regex(self) -> None:
        pat = guess_spam_pattern("Продам канал с подписчиками")
        assert pat is not None
        re.compile(pat)  # не должно бросать

    def test_no_pattern_for_empty(self) -> None:
        assert guess_spam_pattern("да") is None

    def test_does_not_match_unrelated_words(self) -> None:
        """Паттерн не должен ловить несвязанные слова."""
        pat = guess_spam_pattern("Заработок на нейросетях")
        assert not re.search(pat, "зарплата")
        assert not re.search(pat, "подписка")
