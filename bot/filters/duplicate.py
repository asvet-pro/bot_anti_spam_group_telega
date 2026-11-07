"""Детектор повторяющихся сообщений (одинаковые подряд).

Антифлуд ловит только МНОГО сообщений, но не различает «человек пишет
много разного» и «спамер шлёт одно и то же 10 раз подряд». Второе — сам
частый вид спама в чатах, и раньше оно проходило незамеченным.

Логика:
- Нормализуем текст (убираем пробелы, приводим регистр) и считаем хэш.
- Если за окно пользователь отправил текст с тем же хэшем N+1 раз —
  это дубликат, сообщение удаляется.
- Короткие сообщения (короче MIN_DUPLICATE_LEN символов) не считаем:
  «да», «ок», «+» — это нормальный диалог, а не спам.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bot.db import Database


# Сообщения короче этого — не считаем дубликатами (обычная переписка).
MIN_DUPLICATE_LEN = 12


def normalize_text(text: str) -> str:
    """Нормализация для сравнения: регистр, пробелы, пунктуация-«мусор»."""
    # Убираем все пробельные символы и приводим к нижнему регистру.
    return re.sub(r"\s+", "", text.lower())


def text_hash(text: str) -> str:
    """Хэш нормализованного текста."""
    return hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class DuplicateCheck:
    db: Database
    # Сколько одинаковых сообщений подряд считать спамом.
    max_repeats: int
    # За какое окно (сек) смотрим.
    window_seconds: int

    async def is_duplicate(self, user_id: int, chat_id: int, text: str) -> tuple[bool, int]:
        """True, если сообщение — повтор.

        Возвращает (is_duplicate, число таких же сообщений в окне).
        """
        if not text or len(text.strip()) < MIN_DUPLICATE_LEN:
            return False, 0

        h = text_hash(text)
        # Считаем повторы ДО записи текущего сообщения.
        repeats = await self.db.count_repeats(
            user_id=user_id,
            chat_id=chat_id,
            text_hash=h,
            window_seconds=self.window_seconds,
        )
        await self.db.record_message_hash(
            user_id=user_id,
            chat_id=chat_id,
            text_hash=h,
            text_len=len(text),
        )
        # repeats — сколько таких же было раньше. Если repeats >= max_repeats,
        # текущее сообщение — уже N+1-е.
        return repeats >= self.max_repeats, repeats + 1
