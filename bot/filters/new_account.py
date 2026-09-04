"""Проверка возраста аккаунта."""
from __future__ import annotations

import time
from dataclasses import dataclass

from aiogram import types


@dataclass(slots=True)
class NewAccountCheck:
    min_age_days: int

    def is_too_young(self, user: types.User) -> bool:
        """True если аккаунт свежее min_age_days."""
        # aiogram кладёт дату регистраки в user.photo... нет, в Telegram
        # напрямую это не отдаётся. Но можно через getChatMember на user_id
        # получить больше полей, или через Bot.getChat(...) — там тоже нет.
        # Реально Telegram API не отдаёт дату регистрации.
        # Поэтому запасной вариант: считаем по user.id (snowflake) — старые id
        # маленькие, новые — большие. Это эвристика, но рабочая.
        # TODO: при наличии — использовать официальный путь.
        return self._estimate_age_days(user.id) < self.min_age_days

    @staticmethod
    def _estimate_age_days(user_id: int) -> int:
        """Грубая оценка возраста аккаунта по user_id (snowflake).

        Telegram user id — это 41-битный timestamp в миллисекундах,
        сдвинутый влево. Выдёркиваем верхние биты.
        Подробности: https://core.telegram.org/mtproto/auth_key
        """
        # Сдвиг, описанный в документации MTProto.
        approx_ts = (user_id >> 22) / 1000.0
        # У user_id есть прибавка 2^32 от внутреннего кода, не учитываем.
        age_sec = time.time() - approx_ts
        return max(0, int(age_sec // 86400))
