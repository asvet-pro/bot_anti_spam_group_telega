"""Антифлуд-проверка."""
from __future__ import annotations

from dataclasses import dataclass

from bot.db import Database


@dataclass(slots=True)
class FloodCheck:
    db: Database
    max_messages: int
    window_seconds: int

    async def check(self, user_id: int, chat_id: int) -> int:
        """Возвращает текущее количество сообщений в окне."""
        return await self.db.record_message(user_id, chat_id)

    def is_flood(self, count: int) -> bool:
        return count > self.max_messages
