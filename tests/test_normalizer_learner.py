"""Тесты нормализации текста и подбора стоп-слов по разметке."""
from __future__ import annotations

from bot.filters.learner import extract_words, suggest_words
from bot.text_normalizer import normalize_for_ml


class TestNormalizer:
    def test_lowercases(self) -> None:
        assert normalize_for_ml("ПРИВЕТ") == "привет"

    def test_mixes_scripts_to_cyrillic(self) -> None:
        """Латинская буква внутри русского слова → кириллица."""
        assert "заработок" in normalize_for_ml("zаработок")

    def test_keeps_fully_latin_words(self) -> None:
        """Полностью латинское слово остаётся латиницей (chatGPT, SEO)."""
        out = normalize_for_ml("chatGPT и SEO")
        assert "chatgpt" in out
        assert "seo" in out

    def test_removes_spaced_letters(self) -> None:
        """Разрежённое написание схлопывается."""
        out = normalize_for_ml("п.р.и.в.е.т")
        assert out == "привет"

    def test_collapses_repeated_letters(self) -> None:
        """Повторяющиеся буквы схлопываются."""
        out = normalize_for_ml("приввееееет")
        assert out == "привет"

    def test_removes_bot_commands(self) -> None:
        """Команды ботов убираются."""
        assert "/start" not in normalize_for_ml("текст /start@spambot конец")

    def test_removes_urls(self) -> None:
        """Ссылки убираются."""
        out = normalize_for_ml("заработок на https://example.com/promo")
        assert "http" not in out
        assert "заработок" in out

    def test_empty_input(self) -> None:
        assert normalize_for_ml("") == ""


class TestLearner:
    def test_extract_words_filters_short_and_stop(self) -> None:
        words = extract_words("Это как заработок на нейросетях")
        assert "заработок" in words
        assert "нейросетях" in words
        assert "это" not in words  # стоп-слово
        assert "как" not in words  # стоп-слово
        assert all(len(w) >= 4 for w in words)

    def test_suggests_spam_words(self) -> None:
        spam = [
            "Заработок на нейросетях, пиши в личку",
            "Заработок на нейросетях, пиши в личку",
            "Продам курс по заработку на нейросетях",
        ]
        ok = [
            "Привет, как настроить RAG-систему?",
            "Кто пробовал fine-tuning на своих данных?",
        ]
        words = [c.word for c in suggest_words(spam, ok)]
        assert "заработок" in words
        assert "нейросетях" in words

    def test_ignores_words_common_in_normal(self) -> None:
        """Слова, одинаково частые в спаме и в норме, не предлагаются."""
        spam = ["разработка разработка", "разработка разработка"]
        ok = ["разработка", "разработка"]
        words = [c.word for c in suggest_words(spam, ok)]
        assert "разработка" not in words

    def test_requires_min_spam_count(self) -> None:
        """Слово, встретившееся один раз, не предлагается."""
        spam = ["заработок на нейросетях"]
        ok = []
        words = [c.word for c in suggest_words(spam, ok)]
        assert "заработок" not in words

    def test_empty_spam_returns_empty(self) -> None:
        assert suggest_words([], ["что угодно"]) == []
