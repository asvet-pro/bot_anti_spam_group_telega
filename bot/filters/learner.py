"""Подбор стоп-слов по размеченным примерам спама.

Это «обучение без нейросети»: по накопленной разметке (spam_reports)
считаем, какие слова чаще встречаются в спаме, чем в нормальных
сообщениях, и предлагаем добавить их в стоп-лист.

Метод — грубая статистика (log-odds с частотностью):
- для каждого слова считаем частоту в спаме и в норме;
- «информативность» = log((p_spam + eps) / (p_ok + eps));
- слова с высоким log-odds и разумной частотой — кандидаты.

Так работает, когда размеченных примеров ещё немного, и не требует
GPU, обучающего скрипта и целого пайплайна.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

# Слова короче этого не рассматриваем (слишком общие).
MIN_WORD_LEN = 4

# Сколько кандидатов предлагаем за раз.
MAX_SUGGESTIONS = 15

# Стоп-слова, которые не стоит предлагать (общие, не про спам).
_STOP_WORDS = {
    "это", "как", "что", "тебя", "тебе", "если", "для", "этот", "того",
    "тоже", "ещё", "еще", "быть", "весь", "она", "они", "так", "вот",
    "была", "были", "было", "есть", "очень", "просто", "сейчас", "может",
    "когда", "где", "если", "все", "всех", "вам", "вас", "него", "нее",
    "без", "или", "чтобы", "потому", "если", "который", "которая",
    "привет", "здравствуй", "здравствуйте", "спасибо", "пожалуйста",
    "http", "https", "www", "com", "ru", "net", "org", "t", "me",
    "пиши", "пишите", "напиши", "напишите", "вопрос", "вопросы",
}

# Паттерн для извлечения слов.
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{%d,}" % MIN_WORD_LEN)


@dataclass(slots=True)
class WordCandidate:
    """Кандидат в стоп-лист."""

    word: str
    spam_count: int
    ok_count: int
    score: float

    @property
    def display(self) -> str:
        return f"{self.word} ({self.spam_count}/{self.spam_count + self.ok_count})"


def extract_words(text: str) -> list[str]:
    """Извлекает значимые слова из текста (в нижнем регистре)."""
    if not text:
        return []
    words = []
    for w in _WORD_RE.findall(text.lower()):
        if w in _STOP_WORDS:
            continue
        words.append(w)
    return words


def suggest_words(
    spam_texts: list[str],
    ok_texts: list[str],
    limit: int = MAX_SUGGESTIONS,
    min_spam_count: int = 2,
) -> list[WordCandidate]:
    """Предлагает слова в стоп-лист по разметке.

    spam_texts — тексты, помеченные как спам;
    ok_texts   — тексты, помеченные как норма.

    Возвращает кандидатов, отсортированных по убыванию информативности.
    """
    if not spam_texts:
        return []

    spam_counts: Counter[str] = Counter()
    for t in spam_texts:
        spam_counts.update(set(extract_words(t)))

    ok_counts: Counter[str] = Counter()
    for t in ok_texts:
        ok_counts.update(set(extract_words(t)))

    n_spam = max(1, len(spam_texts))
    n_ok = max(1, len(ok_texts))
    eps = 0.5

    candidates: list[WordCandidate] = []
    for word, s_count in spam_counts.items():
        if s_count < min_spam_count:
            continue
        o_count = ok_counts.get(word, 0)

        p_spam = (s_count + eps) / (n_spam + 2 * eps)
        p_ok = (o_count + eps) / (n_ok + 2 * eps)
        score = math.log(p_spam / p_ok)

        # Не предлагаем слова, которые часто встречаются и в норме.
        if o_count > 0 and o_count >= s_count:
            continue

        candidates.append(
            WordCandidate(
                word=word,
                spam_count=s_count,
                ok_count=o_count,
                score=score,
            )
        )

    candidates.sort(key=lambda c: (-c.score, -c.spam_count))
    return candidates[:limit]
